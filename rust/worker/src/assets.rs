//! assets — Inspect and validate run output artifacts.
//!
//! Two CLI commands:
//!   `inspect` — walks a run directory (studio_outputs/<safe_topic>), hashes every
//!     file (SHA-256), records size + mtime, detects duplicates, writes
//!     assets_manifest.json. Fails the manifest write if free disk space <
//!     `--min-free-gb` (default 5 GiB).
//!   `validate` — loads a run_manifest.json and checks that every file
//!     reference (output_video, thumbnail, segments, etc.) exists on disk and
//!     its SHA-256 matches the manifest. In `--strict` mode, warnings become
//!     errors.
//!
//! Used by:
//! - Rust worker: after job success, calls `inspect` to populate manifest.
//! - CI: `validate --strict` on golden directories to catch drift.
//! - Operators: `inspect --json` to script downstream publishing.

use std::collections::BTreeMap;
use std::fs::{self, File};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};

use anyhow::{bail, Context, Result};
use chrono::{DateTime, Utc};
use clap::{Args, Parser, Subcommand};
use serde::{Deserialize, Serialize};
use serde_json::Value;

const ASSETS_MANIFEST_NAME: &str = "assets_manifest.json";
const ASSETS_MANIFEST_TMP_NAME: &str = "assets_manifest.json.tmp";
const DEFAULT_MIN_FREE_GB: f64 = 5.0;
const EXIT_VALIDATION_FAILURE: i32 = 2;

#[derive(Debug, Parser)]
#[command(name = "videoai-assets")]
#[command(about = "Inspect and validate Video.AI run assets")]
pub struct AssetsCli {
    #[command(subcommand)]
    command: AssetsCommand,
}

#[derive(Debug, Subcommand)]
pub enum AssetsCommand {
    /// Inventory a run directory and write assets_manifest.json.
    Inspect(InspectArgs),

    /// Validate run_manifest.json file references.
    Validate(ValidateArgs),
}

#[derive(Debug, Args)]
pub struct InspectArgs {
    /// Run output directory, for example studio_outputs/<safe_topic>.
    #[arg(long)]
    run_dir: PathBuf,

    /// Emit machine-readable JSON summary.
    #[arg(long)]
    json: bool,

    /// Minimum required free disk space in GiB.
    #[arg(long, default_value_t = DEFAULT_MIN_FREE_GB)]
    min_free_gb: f64,
}

#[derive(Debug, Args)]
pub struct ValidateArgs {
    /// Run output directory, for example studio_outputs/<safe_topic>.
    #[arg(long)]
    run_dir: PathBuf,

    /// Manifest to validate. Defaults to <run-dir>/run_manifest.json.
    #[arg(long)]
    manifest: Option<PathBuf>,

    /// Emit machine-readable JSON summary.
    #[arg(long)]
    json: bool,

    /// Treat warnings as validation failures.
    #[arg(long)]
    strict: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct FileEntry {
    path: String,
    bytes: u64,
    sha256: String,
    mtime: String,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct DuplicateEntry {
    sha256: String,
    paths: Vec<String>,
    wasted_bytes: u64,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct DiskReport {
    free_bytes: Option<u64>,
    min_required_bytes: u64,
    ok: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct AssetManifest {
    schema_version: u32,
    generated_at: String,
    run_dir: String,
    total_bytes: u64,
    file_count: usize,
    files: Vec<FileEntry>,
    duplicates: Vec<DuplicateEntry>,
    disk: DiskReport,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct ValidationReport {
    manifest: String,
    checked_references: usize,
    errors: Vec<String>,
    warnings: Vec<String>,
    ok: bool,
}

#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
struct ManifestReference {
    path: String,
    sha256: Option<String>,
}

pub fn run(cli: AssetsCli) -> Result<()> {
    run_command(cli.command)
}

pub fn run_command(command: AssetsCommand) -> Result<()> {
    match command {
        AssetsCommand::Inspect(args) => {
            let manifest = inspect(&args.run_dir, args.min_free_gb)?;
            write_asset_manifest(&args.run_dir, &manifest)?;
            print_inspect(&manifest, args.json)?;

            if !manifest.disk.ok {
                std::process::exit(EXIT_VALIDATION_FAILURE);
            }
        }
        AssetsCommand::Validate(args) => {
            let report = validate(&args.run_dir, args.manifest.as_deref(), args.strict)?;
            print_validation(&report, args.json)?;

            if !report.ok {
                std::process::exit(EXIT_VALIDATION_FAILURE);
            }
        }
    }

    Ok(())
}

pub fn inspect(run_dir: &Path, min_free_gb: f64) -> Result<AssetManifest> {
    ensure_run_dir(run_dir)?;
    let min_required_bytes = gib_to_bytes(min_free_gb)?;

    let mut paths = Vec::new();
    collect_asset_paths(run_dir, run_dir, &mut paths)?;
    paths.sort_by(|a, b| a.to_string_lossy().cmp(&b.to_string_lossy()));

    let mut files = Vec::new();
    for path in paths {
        let metadata =
            fs::metadata(&path).with_context(|| format!("failed to stat {}", path.display()))?;
        let bytes = metadata.len();
        let modified = metadata
            .modified()
            .with_context(|| format!("failed to read mtime for {}", path.display()))?;
        files.push(FileEntry {
            path: relative_slash_path(run_dir, &path)?,
            bytes,
            sha256: hash_file(&path)?,
            mtime: system_time_to_iso(modified),
        });
    }

    files.sort_by(|a, b| a.path.cmp(&b.path));
    let total_bytes = files.iter().map(|entry| entry.bytes).sum();
    let duplicates = find_duplicates(&files);
    let free_bytes = fs2::available_space(run_dir).ok();
    let disk_ok = free_bytes
        .map(|free| free >= min_required_bytes)
        .unwrap_or(true);

    Ok(AssetManifest {
        schema_version: 1,
        generated_at: Utc::now().to_rfc3339(),
        run_dir: run_dir.to_string_lossy().replace('\\', "/"),
        total_bytes,
        file_count: files.len(),
        files,
        duplicates,
        disk: DiskReport {
            free_bytes,
            min_required_bytes,
            ok: disk_ok,
        },
    })
}

pub fn validate(run_dir: &Path, manifest: Option<&Path>, strict: bool) -> Result<ValidationReport> {
    ensure_run_dir(run_dir)?;

    let manifest_path = manifest
        .map(Path::to_path_buf)
        .unwrap_or_else(|| run_dir.join("run_manifest.json"));
    let manifest_text = fs::read_to_string(&manifest_path)
        .with_context(|| format!("failed to read manifest {}", manifest_path.display()))?;
    let manifest_json: Value = serde_json::from_str(&manifest_text)
        .with_context(|| format!("failed to parse manifest {}", manifest_path.display()))?;

    let mut errors = Vec::new();
    let mut warnings = Vec::new();

    if !manifest_json.is_object() {
        errors.push("run_manifest.json must contain a JSON object".to_string());
    }

    let mut references = Vec::new();
    collect_manifest_references(&manifest_json, &mut references);
    references.sort();
    references.dedup();

    if references.is_empty() {
        warnings.push("no local file references found in run_manifest.json".to_string());
    }

    for reference in &references {
        let resolved = resolve_manifest_reference(run_dir, &reference.path);
        if !resolved.exists() {
            errors.push(format!(
                "referenced file is missing: {}",
                reference.path.replace('\\', "/")
            ));
            continue;
        }

        if let Some(expected) = reference.sha256.as_deref() {
            if is_sha256_hex(expected) {
                let actual = hash_file(&resolved)?;
                if !actual.eq_ignore_ascii_case(expected) {
                    errors.push(format!(
                        "sha256 mismatch for {}: expected {}, got {}",
                        reference.path.replace('\\', "/"),
                        expected,
                        actual
                    ));
                }
            }
        }
    }

    let ok = errors.is_empty() && (!strict || warnings.is_empty());

    Ok(ValidationReport {
        manifest: manifest_path.to_string_lossy().replace('\\', "/"),
        checked_references: references.len(),
        errors,
        warnings,
        ok,
    })
}

fn ensure_run_dir(run_dir: &Path) -> Result<()> {
    if !run_dir.is_dir() {
        bail!("run directory not found: {}", run_dir.display());
    }
    Ok(())
}

fn gib_to_bytes(gib: f64) -> Result<u64> {
    if !gib.is_finite() || gib < 0.0 {
        bail!("--min-free-gb must be a non-negative finite number");
    }
    Ok((gib * 1024.0 * 1024.0 * 1024.0).ceil() as u64)
}

fn collect_asset_paths(root: &Path, current: &Path, paths: &mut Vec<PathBuf>) -> Result<()> {
    for entry in fs::read_dir(current)
        .with_context(|| format!("failed to read directory {}", current.display()))?
    {
        let entry =
            entry.with_context(|| format!("failed to read entry in {}", current.display()))?;
        let path = entry.path();
        let file_type = entry
            .file_type()
            .with_context(|| format!("failed to read file type for {}", path.display()))?;

        if file_type.is_dir() {
            collect_asset_paths(root, &path, paths)?;
        } else if file_type.is_file() && !should_skip_asset_file(root, &path)? {
            paths.push(path);
        }
    }

    Ok(())
}

fn should_skip_asset_file(root: &Path, path: &Path) -> Result<bool> {
    if path
        .file_name()
        .and_then(|name| name.to_str())
        .map(|name| name == ASSETS_MANIFEST_NAME || name == ASSETS_MANIFEST_TMP_NAME)
        .unwrap_or(false)
    {
        return Ok(true);
    }

    let relative = relative_slash_path(root, path)?;
    Ok(relative.starts_with(".rust_tmp/"))
}

fn relative_slash_path(root: &Path, path: &Path) -> Result<String> {
    let relative = path
        .strip_prefix(root)
        .with_context(|| format!("failed to relativize {}", path.display()))?;
    Ok(relative.to_string_lossy().replace('\\', "/"))
}

fn system_time_to_iso(time: std::time::SystemTime) -> String {
    let dt: DateTime<Utc> = time.into();
    dt.to_rfc3339()
}

fn hash_file(path: &Path) -> Result<String> {
    let mut file = File::open(path).with_context(|| format!("open {}", path.display()))?;
    let mut hasher = Sha256::new();
    let mut buffer = [0_u8; 64 * 1024];

    loop {
        let read = file
            .read(&mut buffer)
            .with_context(|| format!("read {}", path.display()))?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }

    Ok(hasher.finalize_hex())
}

fn find_duplicates(files: &[FileEntry]) -> Vec<DuplicateEntry> {
    let mut by_hash: BTreeMap<&str, Vec<&FileEntry>> = BTreeMap::new();
    for file in files {
        by_hash.entry(&file.sha256).or_default().push(file);
    }

    by_hash
        .into_iter()
        .filter_map(|(sha256, entries)| {
            if entries.len() < 2 {
                return None;
            }

            let bytes = entries.first().map(|entry| entry.bytes).unwrap_or(0);
            let mut paths = entries
                .iter()
                .map(|entry| entry.path.clone())
                .collect::<Vec<_>>();
            paths.sort();

            Some(DuplicateEntry {
                sha256: sha256.to_string(),
                paths,
                wasted_bytes: bytes.saturating_mul(entries.len().saturating_sub(1) as u64),
            })
        })
        .collect()
}

fn write_asset_manifest(run_dir: &Path, manifest: &AssetManifest) -> Result<()> {
    let target = run_dir.join(ASSETS_MANIFEST_NAME);
    let temp = run_dir.join(ASSETS_MANIFEST_TMP_NAME);
    let content = serde_json::to_string_pretty(manifest)?;

    {
        let mut file =
            File::create(&temp).with_context(|| format!("failed to create {}", temp.display()))?;
        file.write_all(content.as_bytes())
            .with_context(|| format!("failed to write {}", temp.display()))?;
        file.write_all(b"\n")
            .with_context(|| format!("failed to finalize {}", temp.display()))?;
        file.sync_all()
            .with_context(|| format!("failed to sync {}", temp.display()))?;
    }

    if target.exists() {
        fs::remove_file(&target)
            .with_context(|| format!("failed to replace {}", target.display()))?;
    }
    fs::rename(&temp, &target)
        .with_context(|| format!("failed to move {} to {}", temp.display(), target.display()))?;

    Ok(())
}

fn print_inspect(manifest: &AssetManifest, json: bool) -> Result<()> {
    if json {
        println!("{}", serde_json::to_string_pretty(manifest)?);
        return Ok(());
    }

    println!("run_dir: {}", manifest.run_dir);
    println!("files: {}", manifest.file_count);
    println!("total_bytes: {}", manifest.total_bytes);
    println!("duplicates: {}", manifest.duplicates.len());
    match manifest.disk.free_bytes {
        Some(free) => println!(
            "disk_free_bytes: {} (required: {}, ok: {})",
            free, manifest.disk.min_required_bytes, manifest.disk.ok
        ),
        None => println!(
            "disk_free_bytes: unavailable (required: {}, ok: {})",
            manifest.disk.min_required_bytes, manifest.disk.ok
        ),
    }
    println!("wrote: {ASSETS_MANIFEST_NAME}");
    Ok(())
}

fn print_validation(report: &ValidationReport, json: bool) -> Result<()> {
    if json {
        println!("{}", serde_json::to_string_pretty(report)?);
        return Ok(());
    }

    println!("manifest: {}", report.manifest);
    println!("checked_references: {}", report.checked_references);
    println!("errors: {}", report.errors.len());
    for error in &report.errors {
        println!("error: {error}");
    }
    println!("warnings: {}", report.warnings.len());
    for warning in &report.warnings {
        println!("warning: {warning}");
    }
    println!("ok: {}", report.ok);
    Ok(())
}

fn collect_manifest_references(value: &Value, references: &mut Vec<ManifestReference>) {
    match value {
        Value::Object(map) => {
            let path = map.iter().find_map(|(key, value)| {
                if is_path_key(key) {
                    value.as_str().filter(|s| looks_like_local_path(s))
                } else {
                    None
                }
            });

            if let Some(path) = path {
                let sha256 = map
                    .iter()
                    .find_map(|(key, value)| {
                        if is_hash_key(key) {
                            value.as_str()
                        } else {
                            None
                        }
                    })
                    .map(ToString::to_string);

                references.push(ManifestReference {
                    path: path.to_string(),
                    sha256,
                });
            }

            for child in map.values() {
                collect_manifest_references(child, references);
            }
        }
        Value::Array(items) => {
            for item in items {
                collect_manifest_references(item, references);
            }
        }
        _ => {}
    }
}

fn is_path_key(key: &str) -> bool {
    matches!(
        key,
        "path"
            | "file"
            | "file_path"
            | "output_path"
            | "manifest"
            | "thumbnail"
            | "video"
            | "audio"
            | "image"
    )
}

fn is_hash_key(key: &str) -> bool {
    matches!(key, "sha256" | "hash" | "checksum")
}

fn looks_like_local_path(value: &str) -> bool {
    if value.is_empty() {
        return false;
    }
    if value.starts_with("http://") || value.starts_with("https://") {
        return false;
    }

    let lower = value.to_ascii_lowercase();
    value.contains('/')
        || value.contains('\\')
        || lower.ends_with(".mp4")
        || lower.ends_with(".mov")
        || lower.ends_with(".mkv")
        || lower.ends_with(".mp3")
        || lower.ends_with(".wav")
        || lower.ends_with(".flac")
        || lower.ends_with(".png")
        || lower.ends_with(".jpg")
        || lower.ends_with(".jpeg")
        || lower.ends_with(".webp")
        || lower.ends_with(".srt")
        || lower.ends_with(".json")
        || lower.ends_with(".txt")
}

fn resolve_manifest_reference(run_dir: &Path, reference: &str) -> PathBuf {
    let path = PathBuf::from(reference);
    if path.is_absolute() {
        path
    } else {
        run_dir.join(path)
    }
}

fn is_sha256_hex(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|b| b.is_ascii_hexdigit())
}

struct Sha256 {
    state: [u32; 8],
    buffer: [u8; 64],
    buffer_len: usize,
    bit_len: u64,
}

impl Sha256 {
    fn new() -> Self {
        Self {
            state: [
                0x6a09_e667,
                0xbb67_ae85,
                0x3c6e_f372,
                0xa54f_f53a,
                0x510e_527f,
                0x9b05_688c,
                0x1f83_d9ab,
                0x5be0_cd19,
            ],
            buffer: [0; 64],
            buffer_len: 0,
            bit_len: 0,
        }
    }

    fn update(&mut self, mut input: &[u8]) {
        self.bit_len = self
            .bit_len
            .wrapping_add((input.len() as u64).wrapping_mul(8));

        if self.buffer_len > 0 {
            let to_copy = (64 - self.buffer_len).min(input.len());
            self.buffer[self.buffer_len..self.buffer_len + to_copy]
                .copy_from_slice(&input[..to_copy]);
            self.buffer_len += to_copy;
            input = &input[to_copy..];

            if self.buffer_len == 64 {
                let block = self.buffer;
                self.compress(&block);
                self.buffer_len = 0;
            }
        }

        while input.len() >= 64 {
            let mut block = [0_u8; 64];
            block.copy_from_slice(&input[..64]);
            self.compress(&block);
            input = &input[64..];
        }

        if !input.is_empty() {
            self.buffer[..input.len()].copy_from_slice(input);
            self.buffer_len = input.len();
        }
    }

    fn finalize_hex(mut self) -> String {
        const HEX: &[u8; 16] = b"0123456789abcdef";

        self.buffer[self.buffer_len] = 0x80;
        self.buffer_len += 1;

        if self.buffer_len > 56 {
            self.buffer[self.buffer_len..].fill(0);
            let block = self.buffer;
            self.compress(&block);
            self.buffer_len = 0;
        }

        self.buffer[self.buffer_len..56].fill(0);
        self.buffer[56..64].copy_from_slice(&self.bit_len.to_be_bytes());
        let block = self.buffer;
        self.compress(&block);

        let mut out = String::with_capacity(64);
        for word in self.state {
            for byte in word.to_be_bytes() {
                out.push(HEX[usize::from(byte >> 4)] as char);
                out.push(HEX[usize::from(byte & 0x0f)] as char);
            }
        }
        out
    }

    fn compress(&mut self, block: &[u8; 64]) {
        const K: [u32; 64] = [
            0x428a_2f98,
            0x7137_4491,
            0xb5c0_fbcf,
            0xe9b5_dba5,
            0x3956_c25b,
            0x59f1_11f1,
            0x923f_82a4,
            0xab1c_5ed5,
            0xd807_aa98,
            0x1283_5b01,
            0x2431_85be,
            0x550c_7dc3,
            0x72be_5d74,
            0x80de_b1fe,
            0x9bdc_06a7,
            0xc19b_f174,
            0xe49b_69c1,
            0xefbe_4786,
            0x0fc1_9dc6,
            0x240c_a1cc,
            0x2de9_2c6f,
            0x4a74_84aa,
            0x5cb0_a9dc,
            0x76f9_88da,
            0x983e_5152,
            0xa831_c66d,
            0xb003_27c8,
            0xbf59_7fc7,
            0xc6e0_0bf3,
            0xd5a7_9147,
            0x06ca_6351,
            0x1429_2967,
            0x27b7_0a85,
            0x2e1b_2138,
            0x4d2c_6dfc,
            0x5338_0d13,
            0x650a_7354,
            0x766a_0abb,
            0x81c2_c92e,
            0x9272_2c85,
            0xa2bf_e8a1,
            0xa81a_664b,
            0xc24b_8b70,
            0xc76c_51a3,
            0xd192_e819,
            0xd699_0624,
            0xf40e_3585,
            0x106a_a070,
            0x19a4_c116,
            0x1e37_6c08,
            0x2748_774c,
            0x34b0_bcb5,
            0x391c_0cb3,
            0x4ed8_aa4a,
            0x5b9c_ca4f,
            0x682e_6ff3,
            0x748f_82ee,
            0x78a5_636f,
            0x84c8_7814,
            0x8cc7_0208,
            0x90be_fffa,
            0xa450_6ceb,
            0xbef9_a3f7,
            0xc671_78f2,
        ];

        let mut w = [0_u32; 64];
        for (i, chunk) in block.chunks_exact(4).take(16).enumerate() {
            w[i] = u32::from_be_bytes([chunk[0], chunk[1], chunk[2], chunk[3]]);
        }
        for i in 16..64 {
            let s0 = w[i - 15].rotate_right(7) ^ w[i - 15].rotate_right(18) ^ (w[i - 15] >> 3);
            let s1 = w[i - 2].rotate_right(17) ^ w[i - 2].rotate_right(19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16]
                .wrapping_add(s0)
                .wrapping_add(w[i - 7])
                .wrapping_add(s1);
        }

        let mut a = self.state[0];
        let mut b = self.state[1];
        let mut c = self.state[2];
        let mut d = self.state[3];
        let mut e = self.state[4];
        let mut f = self.state[5];
        let mut g = self.state[6];
        let mut h = self.state[7];

        for i in 0..64 {
            let s1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
            let ch = (e & f) ^ ((!e) & g);
            let temp1 = h
                .wrapping_add(s1)
                .wrapping_add(ch)
                .wrapping_add(K[i])
                .wrapping_add(w[i]);
            let s0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
            let maj = (a & b) ^ (a & c) ^ (b & c);
            let temp2 = s0.wrapping_add(maj);

            h = g;
            g = f;
            f = e;
            e = d.wrapping_add(temp1);
            d = c;
            c = b;
            b = a;
            a = temp1.wrapping_add(temp2);
        }

        self.state[0] = self.state[0].wrapping_add(a);
        self.state[1] = self.state[1].wrapping_add(b);
        self.state[2] = self.state[2].wrapping_add(c);
        self.state[3] = self.state[3].wrapping_add(d);
        self.state[4] = self.state[4].wrapping_add(e);
        self.state[5] = self.state[5].wrapping_add(f);
        self.state[6] = self.state[6].wrapping_add(g);
        self.state[7] = self.state[7].wrapping_add(h);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn write_file(path: &Path, bytes: &[u8]) -> Result<()> {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        fs::write(path, bytes)?;
        Ok(())
    }

    #[test]
    fn hash_file_returns_known_sha256() -> Result<()> {
        let temp_dir = tempfile::tempdir()?;
        let path = temp_dir.path().join("hello.txt");
        write_file(&path, b"hello")?;

        let hash = hash_file(&path)?;

        assert_eq!(
            hash,
            "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        );
        Ok(())
    }

    #[test]
    fn sha256_handles_multi_block_inputs() {
        let mut hasher = Sha256::new();
        let input = [b'a'; 1_000];
        hasher.update(&input);

        assert_eq!(
            hasher.finalize_hex(),
            "41edece42d63e8d9bf515a9ba6932e1c20cbc9f5a5d134645adb5db1b9737ea3"
        );
    }

    #[test]
    fn inspect_detects_duplicates_and_uses_relative_paths() -> Result<()> {
        let temp_dir = tempfile::tempdir()?;
        write_file(&temp_dir.path().join("segments").join("a.mp4"), b"same")?;
        write_file(&temp_dir.path().join("segments").join("b.mp4"), b"same")?;
        write_file(&temp_dir.path().join("thumb.png"), b"different")?;

        let manifest = inspect(temp_dir.path(), 0.0)?;

        assert_eq!(manifest.file_count, 3);
        assert_eq!(manifest.duplicates.len(), 1);
        assert_eq!(
            manifest.duplicates[0].paths,
            vec!["segments/a.mp4".to_string(), "segments/b.mp4".to_string()]
        );
        assert_eq!(manifest.duplicates[0].wasted_bytes, 4);
        assert!(manifest
            .files
            .iter()
            .all(|entry| !entry.path.contains('\\')));
        Ok(())
    }

    #[test]
    fn inspect_marks_disk_guardrail_failure() -> Result<()> {
        let temp_dir = tempfile::tempdir()?;
        write_file(&temp_dir.path().join("out.mp4"), b"video")?;

        let manifest = inspect(temp_dir.path(), f64::MAX)?;

        assert!(!manifest.disk.ok);
        Ok(())
    }

    #[test]
    fn write_asset_manifest_skips_previous_asset_manifest_on_next_inspect() -> Result<()> {
        let temp_dir = tempfile::tempdir()?;
        write_file(&temp_dir.path().join("out.mp4"), b"video")?;

        let first = inspect(temp_dir.path(), 0.0)?;
        write_asset_manifest(temp_dir.path(), &first)?;
        let second = inspect(temp_dir.path(), 0.0)?;

        assert_eq!(first.file_count, second.file_count);
        assert_eq!(
            first
                .files
                .iter()
                .map(|entry| entry.path.clone())
                .collect::<Vec<_>>(),
            second
                .files
                .iter()
                .map(|entry| entry.path.clone())
                .collect::<Vec<_>>()
        );
        Ok(())
    }

    #[test]
    fn validate_passes_for_existing_manifest_references() -> Result<()> {
        let temp_dir = tempfile::tempdir()?;
        write_file(&temp_dir.path().join("out.mp4"), b"video")?;
        let hash = hash_file(&temp_dir.path().join("out.mp4"))?;
        let manifest = json!({
            "outputs": [
                { "path": "out.mp4", "sha256": hash }
            ]
        });
        fs::write(
            temp_dir.path().join("run_manifest.json"),
            serde_json::to_string_pretty(&manifest)?,
        )?;

        let report = validate(temp_dir.path(), None, false)?;

        assert!(report.ok);
        assert_eq!(report.checked_references, 1);
        assert!(report.errors.is_empty());
        Ok(())
    }

    #[test]
    fn validate_fails_for_missing_manifest_reference() -> Result<()> {
        let temp_dir = tempfile::tempdir()?;
        let manifest = json!({
            "outputs": [
                { "path": "missing.mp4" }
            ]
        });
        fs::write(
            temp_dir.path().join("run_manifest.json"),
            serde_json::to_string_pretty(&manifest)?,
        )?;

        let report = validate(temp_dir.path(), None, false)?;

        assert!(!report.ok);
        assert_eq!(report.checked_references, 1);
        assert_eq!(report.errors.len(), 1);
        assert!(report.errors[0].contains("missing.mp4"));
        Ok(())
    }

    #[test]
    fn validate_strict_fails_when_no_file_references_are_found() -> Result<()> {
        let temp_dir = tempfile::tempdir()?;
        fs::write(temp_dir.path().join("run_manifest.json"), "{}")?;

        let report = validate(temp_dir.path(), None, true)?;

        assert!(!report.ok);
        assert!(report.errors.is_empty());
        assert_eq!(report.warnings.len(), 1);
        Ok(())
    }

    #[test]
    fn validate_detects_hash_mismatch() -> Result<()> {
        let temp_dir = tempfile::tempdir()?;
        write_file(&temp_dir.path().join("out.mp4"), b"video")?;
        let manifest = json!({
            "outputs": [
                {
                    "path": "out.mp4",
                    "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
                }
            ]
        });
        fs::write(
            temp_dir.path().join("run_manifest.json"),
            serde_json::to_string_pretty(&manifest)?,
        )?;

        let report = validate(temp_dir.path(), None, false)?;

        assert!(!report.ok);
        assert_eq!(report.errors.len(), 1);
        assert!(report.errors[0].contains("sha256 mismatch"));
        Ok(())
    }
}

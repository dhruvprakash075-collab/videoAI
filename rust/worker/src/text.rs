use std::fs;
use std::path::PathBuf;

use anyhow::{bail, Context, Result};
use clap::{Args, Subcommand, ValueEnum};
use serde::Serialize;

const DEFAULT_TARGET_WORDS: usize = 100;

#[derive(Debug, Subcommand)]
pub enum TextCommand {
    /// Split source text into per-segment chunks.
    Split(TextSplitArgs),
}

#[derive(Clone, Debug, Args)]
pub struct TextSplitArgs {
    /// Source text file to split.
    #[arg(long)]
    pub input: PathBuf,

    /// Source type, used for chapter-heading behavior.
    #[arg(long, default_value = "txt")]
    pub source_type: String,

    /// Split strategy. Rust supports by_word_count and by_chapter; by_llm falls back to by_word_count.
    #[arg(long, value_enum, default_value_t = SplitStrategy::ByWordCount)]
    pub strategy: SplitStrategy,

    /// Target words per raw chunk before final rebalance.
    #[arg(long, default_value_t = DEFAULT_TARGET_WORDS)]
    pub target_words: usize,

    /// Number of output chunks to return.
    #[arg(long)]
    pub n_segments: usize,

    /// Emit machine-readable JSON. Accepted for consistency with other worker subcommands.
    #[arg(long)]
    pub json: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
pub enum SplitStrategy {
    #[value(name = "by_word_count")]
    ByWordCount,
    #[value(name = "by_chapter")]
    ByChapter,
    #[value(name = "by_llm")]
    ByLlm,
}

#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
pub struct SegmentChunk {
    pub text: String,
    pub b_roll_hint: String,
    pub key_event: String,
    pub index: usize,
    pub source_chapter: String,
}

#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
pub struct TextSplitReport {
    pub strategy: String,
    pub source_type: String,
    pub target_words: usize,
    pub n_segments: usize,
    pub chunks: Vec<SegmentChunk>,
    pub warnings: Vec<String>,
}

pub fn run_command(command: TextCommand) -> Result<()> {
    match command {
        TextCommand::Split(args) => {
            let report = split_path(&args)?;
            println!("{}", serde_json::to_string_pretty(&report)?);
        }
    }
    Ok(())
}

pub fn split_path(args: &TextSplitArgs) -> Result<TextSplitReport> {
    if args.n_segments == 0 {
        bail!("n_segments must be > 0");
    }
    let text = fs::read_to_string(&args.input)
        .with_context(|| format!("failed to read source text {}", args.input.display()))?;
    Ok(split_text(
        &text,
        &args.source_type,
        args.strategy,
        args.n_segments,
        args.target_words,
    ))
}

pub fn split_text(
    text: &str,
    source_type: &str,
    strategy: SplitStrategy,
    n_segments: usize,
    target_words: usize,
) -> TextSplitReport {
    let target_words = target_words.max(1);
    let mut warnings = Vec::new();
    let chunks = if text.trim().is_empty() {
        empty_chunks(n_segments)
    } else {
        let chunks = match strategy {
            SplitStrategy::ByChapter => {
                let chunks = split_by_chapter(text, source_type);
                if chunks.is_empty() {
                    warnings.push(format!(
                        "No headings in {source_type} source; fell back to by_word_count"
                    ));
                    split_by_word_count(text, target_words)
                } else {
                    chunks
                }
            }
            SplitStrategy::ByWordCount => split_by_word_count(text, target_words),
            SplitStrategy::ByLlm => {
                warnings.push("by_llm is kept in Python; fell back to by_word_count".to_string());
                split_by_word_count(text, target_words)
            }
        };
        if chunks.is_empty() {
            empty_chunks(n_segments)
        } else if chunks.len() == n_segments {
            index_chunks(chunks)
        } else {
            rebalance(chunks, n_segments)
        }
    };

    TextSplitReport {
        strategy: strategy_name(strategy).to_string(),
        source_type: source_type.to_string(),
        target_words,
        n_segments,
        chunks,
        warnings,
    }
}

fn split_by_word_count(text: &str, target_words: usize) -> Vec<SegmentChunk> {
    let sentences = split_sentences(text);
    if sentences.is_empty() {
        return Vec::new();
    }

    let mut raw = Vec::new();
    let mut buf: Vec<String> = Vec::new();
    let mut buf_words = 0usize;
    for sent in sentences {
        let sent_words = word_count(&sent);
        if !buf.is_empty()
            && (buf_words + sent_words) > target_words
            && (buf_words as f64) >= (target_words as f64 * 0.5)
        {
            raw.push(chunk(buf.join(" ").trim().to_string()));
            buf = vec![sent];
            buf_words = sent_words;
        } else {
            buf.push(sent);
            buf_words += sent_words;
        }
    }
    if !buf.is_empty() {
        raw.push(chunk(buf.join(" ").trim().to_string()));
    }
    raw
}

fn split_by_chapter(text: &str, source_type: &str) -> Vec<SegmentChunk> {
    if source_type != "md" {
        return Vec::new();
    }
    let boundaries = markdown_heading_boundaries(text);
    if boundaries.is_empty() {
        return Vec::new();
    }

    let mut chunks = Vec::new();
    for (idx, (_start, end, title)) in boundaries.iter().enumerate() {
        let section_end = boundaries
            .get(idx + 1)
            .map(|(next_start, _, _)| *next_start)
            .unwrap_or(text.len());
        let body = text[*end..section_end].trim();
        if !body.is_empty() {
            chunks.push(SegmentChunk {
                text: body.to_string(),
                b_roll_hint: String::new(),
                key_event: String::new(),
                index: 0,
                source_chapter: title.clone(),
            });
        }
    }
    chunks
}

fn rebalance(mut chunks: Vec<SegmentChunk>, n: usize) -> Vec<SegmentChunk> {
    if n == 0 {
        return Vec::new();
    }
    if chunks.is_empty() {
        return empty_chunks(n);
    }

    while chunks.len() > n {
        let merge_idx = (0..chunks.len() - 1)
            .min_by_key(|idx| word_count(&chunks[*idx].text) + word_count(&chunks[*idx + 1].text))
            .unwrap_or(0);
        let a = chunks[merge_idx].clone();
        let b = chunks[merge_idx + 1].clone();
        let merged = SegmentChunk {
            text: format!("{}\n\n{}", a.text, b.text).trim().to_string(),
            b_roll_hint: first_non_empty(&a.b_roll_hint, &b.b_roll_hint),
            key_event: first_non_empty(&a.key_event, &b.key_event),
            source_chapter: first_non_empty(&a.source_chapter, &b.source_chapter),
            index: 0,
        };
        chunks.splice(merge_idx..=merge_idx + 1, [merged]);
    }

    while chunks.len() < n {
        let split_idx = (0..chunks.len())
            .max_by_key(|idx| word_count(&chunks[*idx].text))
            .unwrap_or(0);
        let big = chunks[split_idx].clone();
        let sentences = split_sentences(&big.text);
        if sentences.len() < 2 {
            break;
        }
        let mid = sentences.len() / 2;
        let left = SegmentChunk {
            text: sentences[..mid].join(" ").trim().to_string(),
            b_roll_hint: big.b_roll_hint.clone(),
            key_event: big.key_event.clone(),
            source_chapter: big.source_chapter.clone(),
            index: 0,
        };
        let right = SegmentChunk {
            text: sentences[mid..].join(" ").trim().to_string(),
            b_roll_hint: big.b_roll_hint,
            key_event: big.key_event,
            source_chapter: big.source_chapter,
            index: 0,
        };
        chunks.splice(split_idx..=split_idx, [left, right]);
    }

    while chunks.len() < n {
        chunks.push(chunk(String::new()));
    }

    index_chunks(chunks)
}

fn split_sentences(text: &str) -> Vec<String> {
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return Vec::new();
    }

    let mut parts = Vec::new();
    let mut start = 0usize;
    let mut prev_sentence_end = false;
    for (idx, ch) in trimmed.char_indices() {
        let should_split = ch == '\n' || (prev_sentence_end && ch.is_whitespace());
        if should_split {
            let part = trimmed[start..idx].trim();
            if !part.is_empty() {
                parts.push(part.to_string());
            }
            start = idx + ch.len_utf8();
            prev_sentence_end = false;
            continue;
        }
        prev_sentence_end = matches!(ch, '.' | '!' | '?' | '\u{0964}');
    }
    let tail = trimmed[start..].trim();
    if !tail.is_empty() {
        parts.push(tail.to_string());
    }
    parts
}

fn markdown_heading_boundaries(text: &str) -> Vec<(usize, usize, String)> {
    let mut boundaries = Vec::new();
    let mut offset = 0usize;
    for line in text.split_inclusive('\n') {
        let line_without_newline = line.trim_end_matches(['\r', '\n']);
        let trimmed_end = line_without_newline.trim_end();
        let hashes = trimmed_end.chars().take_while(|ch| *ch == '#').count();
        let is_h1_or_h2 = (hashes == 1 || hashes == 2)
            && trimmed_end
                .as_bytes()
                .get(hashes)
                .is_some_and(u8::is_ascii_whitespace);
        if is_h1_or_h2 {
            let title = trimmed_end[hashes..].trim().to_string();
            if !title.is_empty() {
                boundaries.push((offset, offset + line_without_newline.len(), title));
            }
        }
        offset += line.len();
    }
    boundaries
}

fn index_chunks(mut chunks: Vec<SegmentChunk>) -> Vec<SegmentChunk> {
    for (idx, chunk) in chunks.iter_mut().enumerate() {
        chunk.index = idx;
    }
    chunks
}

fn empty_chunks(n: usize) -> Vec<SegmentChunk> {
    index_chunks((0..n).map(|_| chunk(String::new())).collect())
}

fn chunk(text: String) -> SegmentChunk {
    SegmentChunk {
        text,
        b_roll_hint: String::new(),
        key_event: String::new(),
        index: 0,
        source_chapter: String::new(),
    }
}

fn word_count(text: &str) -> usize {
    text.split_whitespace().count()
}

fn first_non_empty(a: &str, b: &str) -> String {
    if a.is_empty() {
        b.to_string()
    } else {
        a.to_string()
    }
}

fn strategy_name(strategy: SplitStrategy) -> &'static str {
    match strategy {
        SplitStrategy::ByWordCount => "by_word_count",
        SplitStrategy::ByChapter => "by_chapter",
        SplitStrategy::ByLlm => "by_llm",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn splits_devanagari_danda_sentences() {
        let parts = split_sentences("पहला वाक्य। दूसरा वाक्य. Third!");
        assert_eq!(parts, vec!["पहला वाक्य।", "दूसरा वाक्य.", "Third!"]);
    }

    #[test]
    fn word_count_uses_half_target_hysteresis() {
        let text = "one two. three four five six seven. eight nine.";
        let chunks = split_by_word_count(text, 6);
        assert_eq!(chunks.len(), 2);
        assert_eq!(chunks[0].text, "one two. three four five six seven.");
        assert_eq!(chunks[1].text, "eight nine.");
    }

    #[test]
    fn chapter_split_parses_h1_h2_boundaries() {
        let text = "# One\nAlpha.\n## Two\nBeta.\n### Ignored\nGamma.";
        let report = split_text(text, "md", SplitStrategy::ByChapter, 2, 100);
        assert_eq!(report.chunks.len(), 2);
        assert_eq!(report.chunks[0].source_chapter, "One");
        assert_eq!(report.chunks[0].text, "Alpha.");
        assert_eq!(report.chunks[1].source_chapter, "Two");
        assert!(report.chunks[1].text.contains("### Ignored"));
    }

    #[test]
    fn chapter_split_falls_back_for_txt() {
        let report = split_text("One. Two.", "txt", SplitStrategy::ByChapter, 2, 100);
        assert_eq!(report.chunks.len(), 2);
        assert_eq!(report.chunks[0].text, "One.");
        assert!(report.warnings[0].contains("fell back"));
    }

    #[test]
    fn by_llm_falls_back_to_word_count() {
        let report = split_text("One. Two.", "txt", SplitStrategy::ByLlm, 2, 100);
        assert_eq!(report.chunks.len(), 2);
        assert_eq!(report.strategy, "by_llm");
        assert!(report.warnings[0].contains("fell back"));
    }

    #[test]
    fn rebalance_merges_smallest_adjacent_pair() {
        let chunks = vec![
            chunk("one".to_string()),
            chunk("two".to_string()),
            chunk("three four five".to_string()),
        ];
        let out = rebalance(chunks, 2);
        assert_eq!(out.len(), 2);
        assert_eq!(out[0].text, "one\n\ntwo");
        assert_eq!(out[0].index, 0);
        assert_eq!(out[1].index, 1);
    }

    #[test]
    fn rebalance_splits_largest_at_mid_sentence() {
        let chunks = vec![chunk("A. B. C. D.".to_string())];
        let out = rebalance(chunks, 2);
        assert_eq!(out.len(), 2);
        assert_eq!(out[0].text, "A. B.");
        assert_eq!(out[1].text, "C. D.");
    }

    #[test]
    fn empty_input_pads_to_n() {
        let report = split_text("   ", "txt", SplitStrategy::ByWordCount, 3, 100);
        assert_eq!(report.chunks.len(), 3);
        assert!(report.chunks.iter().all(|chunk| chunk.text.is_empty()));
    }
}

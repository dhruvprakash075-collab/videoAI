//! Native Phase-1 GUI shell (feature `gui`).
//!
//! An `eframe`/`egui` window that owns the engine: it supervises the single
//! job worker subprocess (bugs #3/#7/#21), reads the queue DB directly via
//! `rusqlite`, and provides Jobs / Create-Job / Director Canvas panels. The
//! web dashboard stays as a fallback; this shell is the one-click entry point
//! (bugs #1/#2/#4/#5/#34).

pub mod engine;

use std::path::{Path, PathBuf};
use std::time::Duration;

use anyhow::Result;
use eframe::egui;

use engine::{
    create_job, dash_stats, get_events, get_job, host_reachable, list_jobs, open_in_explorer,
    open_in_player, request_cancel, retry_job, EventRec, JobRec, SupervisedWorker,
};

#[derive(Clone, Copy, PartialEq, Eq)]
enum View {
    Jobs,
    CreateJob,
    Canvas,
}

struct FormState {
    topic: String,
    run_mode: String,
    segment_count: String,
    images_per_segment: String,
    duration_minutes: String,
    dry_run: bool,
    skip_preflight: bool,
    error: Option<String>,
    submitted: Option<String>,
}

impl Default for FormState {
    fn default() -> Self {
        Self {
            topic: String::new(),
            run_mode: "".to_string(),
            segment_count: "1".to_string(),
            images_per_segment: "2".to_string(),
            duration_minutes: String::new(),
            dry_run: false,
            skip_preflight: false,
            error: None,
            submitted: None,
        }
    }
}

pub struct VideoApp {
    root: PathBuf,
    worker: SupervisedWorker,
    view: View,
    jobs: Vec<JobRec>,
    selected_job: Option<i64>,
    detail_events: Vec<EventRec>,
    canvas_job: Option<JobRec>,
    status: Status,
    form: FormState,
    tick: u64,
}

#[derive(Default)]
struct Status {
    worker_alive: bool,
    worker_reason: String,
    comfyui_up: bool,
    comfyui_addr: String,
    ollama_up: bool,
    stats_error: Option<String>,
}

impl VideoApp {
    pub fn new(root: PathBuf) -> Self {
        let mut worker = SupervisedWorker::new(&root);
        let spawn_ok = worker.ensure_running().is_ok();
        let (host, port) = engine::comfyui_addr(&root);
        let comfyui_up = host_reachable(&host, port, 300);
        let ollama_up = host_reachable("127.0.0.1", 11434, 300);
        let status = Status {
            worker_alive: spawn_ok,
            worker_reason: if spawn_ok {
                "spawned".to_string()
            } else {
                "spawn failed".to_string()
            },
            comfyui_up,
            comfyui_addr: format!("{host}:{port}"),
            ollama_up,
            stats_error: None,
        };
        Self {
            root,
            worker,
            view: View::Jobs,
            jobs: Vec::new(),
            selected_job: None,
            detail_events: Vec::new(),
            canvas_job: None,
            status,
            form: FormState::default(),
            tick: 0,
        }
    }

    fn refresh(&mut self) {
        // Refresh every ~1s via request_repaint_after in update().
        if let Ok(jobs) = list_jobs(&self.root, 100) {
            self.jobs = jobs;
            self.status.stats_error = None;
        } else if self.jobs.is_empty() {
            self.status.stats_error = None;
        }
        self.status.worker_alive = self.worker.is_alive();
        let (host, port) = engine::comfyui_addr(&self.root);
        self.status.comfyui_up = host_reachable(&host, port, 300);
        self.status.ollama_up = host_reachable("127.0.0.1", 11434, 300);

        let sel = self.selected_job;
        if let Some(job_id) = sel {
            if let Ok(events) = get_events(&self.root, job_id, 500) {
                self.detail_events = events;
            }
        }

        if let Some(job_id) = self.selected_job {
            self.canvas_job = get_job(&self.root, job_id).unwrap_or(None);
        } else if let Some(first) = self.jobs.first() {
            self.canvas_job = get_job(&self.root, first.id).unwrap_or(None);
            self.selected_job = Some(first.id);
        }
    }

    fn ensure_worker(&mut self) {
        if !self.status.worker_alive {
            let ok = self.worker.ensure_running().is_ok();
            self.status.worker_alive = ok;
            self.status.worker_reason = if ok {
                "spawned".to_string()
            } else {
                "spawn failed".to_string()
            };
        }
    }

    fn submit_job(&mut self) {
        let f = &mut self.form;
        let segment_count: u64 = f.segment_count.trim().parse().unwrap_or(1);
        let images_per_segment: u64 = f.images_per_segment.trim().parse().unwrap_or(2);
        let mut fields: Vec<(&str, serde_json::Value)> = vec![
            (
                "topic",
                serde_json::Value::String(f.topic.trim().to_string()),
            ),
            (
                "segment_count",
                serde_json::Value::Number(segment_count.into()),
            ),
            (
                "images_per_segment",
                serde_json::Value::Number(images_per_segment.into()),
            ),
            ("no_resume", serde_json::Value::Bool(true)),
        ];
        if !f.run_mode.trim().is_empty() {
            fields.push((
                "run_mode",
                serde_json::Value::String(f.run_mode.trim().to_string()),
            ));
        }
        if !f.duration_minutes.trim().is_empty() {
            if let Ok(secs) = f.duration_minutes.trim().parse::<u64>() {
                fields.push(("duration", serde_json::Value::Number(secs.into())));
            }
        }
        if f.dry_run {
            fields.push(("dry_run", serde_json::Value::Bool(true)));
        }
        if f.skip_preflight {
            fields.push(("skip_preflight", serde_json::Value::Bool(true)));
        }

        let base = match engine::build_request_json(&fields) {
            Ok(v) => v,
            Err(e) => {
                f.error = Some(format!("invalid request: {e:?}"));
                f.submitted = None;
                return;
            }
        };
        let topic = f.topic.trim().to_string();
        match create_job(&self.root, &base, Some(&topic), None, None) {
            Ok(id) => {
                let topic = topic.clone();
                f.submitted = Some(format!("Job #{id} queued — topic {topic}"));
                f.error = None;
                f.topic.clear();
            }
            Err(e) => {
                f.error = Some(format!("queue failed: {e:#}"));
                f.submitted = None;
            }
        }
    }
}

impl eframe::App for VideoApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        self.tick += 1;
        if self.tick % 60 == 0 {
            self.ensure_worker();
            self.refresh();
        }
        ctx.request_repaint_after(Duration::from_millis(1000));

        egui::TopBottomPanel::bottom("status_bar").show(ctx, |ui| {
            ui.horizontal(|ui| {
                let (color, text) = if self.status.worker_alive {
                    (egui::Color32::from_rgb(90, 200, 120), "worker ●")
                } else {
                    (egui::Color32::from_rgb(220, 120, 120), "worker ○")
                };
                ui.label(egui::RichText::new(text).color(color));
                let (color, text) = if self.status.comfyui_up {
                    (
                        egui::Color32::from_rgb(90, 200, 120),
                        self.status.comfyui_addr.as_str(),
                    )
                } else {
                    (egui::Color32::from_rgb(220, 120, 120), "comfyui ○")
                };
                ui.label(egui::RichText::new(text).color(color));
                let (color, text) = if self.status.ollama_up {
                    (egui::Color32::from_rgb(90, 200, 120), "ollama ●")
                } else {
                    (egui::Color32::from_rgb(220, 120, 120), "ollama ○")
                };
                ui.label(egui::RichText::new(text).color(color));
                if let Ok(stats) = dash_stats(&self.root) {
                    ui.separator();
                    ui.label(format!(
                        "{} queued · {} running · {} done",
                        stats.queued, stats.running, stats.succeeded
                    ));
                }
                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                    if ui.button("Start worker").clicked() {
                        self.ensure_worker();
                    }
                });
            });
        });

        egui::SidePanel::left("nav")
            .resizable(false)
            .default_width(180.0)
            .show(ctx, |ui| {
                ui.add_space(8.0);
                ui.heading("Video.AI");
                ui.add_space(4.0);
                if ui
                    .selectable_label(self.view == View::Jobs, "Jobs")
                    .clicked()
                {
                    self.view = View::Jobs;
                }
                if ui
                    .selectable_label(self.view == View::CreateJob, "Create Job")
                    .clicked()
                {
                    self.view = View::CreateJob;
                }
                if ui
                    .selectable_label(self.view == View::Canvas, "Director Canvas")
                    .clicked()
                {
                    self.view = View::Canvas;
                }
                ui.add_space(12.0);
                ui.weak(format!("root: {}", self.root.display()));
            });

        egui::CentralPanel::default().show(ctx, |ui| match self.view {
            View::Jobs => self.panel_jobs(ui),
            View::CreateJob => self.panel_create(ui),
            View::Canvas => self.panel_canvas(ui),
        });
    }
}

fn status_color(status: &str) -> egui::Color32 {
    match status {
        "succeeded" => egui::Color32::from_rgb(90, 200, 120),
        "failed" => egui::Color32::from_rgb(220, 120, 120),
        "running" | "paused" => egui::Color32::from_rgb(230, 180, 90),
        "queued" => egui::Color32::from_rgb(150, 160, 180),
        _ => egui::Color32::GRAY,
    }
}

impl VideoApp {
    fn panel_jobs(&mut self, ui: &mut egui::Ui) {
        ui.heading("Jobs");
        let mut requested_cancel: Option<i64> = None;
        let mut requested_retry: Option<i64> = None;
        egui::ScrollArea::vertical()
            .auto_shrink([false, false])
            .show(ui, |ui| {
                let jobs = self.jobs.clone();
                for job in jobs {
                    let job_id = job.id;
                    ui.horizontal(|ui| {
                        ui.label(format!("#{}", job.id));
                        ui.label(
                            egui::RichText::new(job.status.as_str())
                                .color(status_color(&job.status)),
                        );
                        ui.label(job.topic.clone().unwrap_or_default());
                        if self.selected_job == Some(job_id) {
                            ui.label("◄");
                        }
                        if ui.small_button("select").clicked() {
                            self.selected_job = Some(job_id);
                        }
                        if let Some(out) = job.output_path.as_deref() {
                            if ui.small_button("open").clicked() {
                                open_in_explorer(Path::new(out));
                            }
                        }
                        if ui.small_button("cancel").clicked() {
                            requested_cancel = Some(job_id);
                        }
                        if ui.small_button("retry").clicked() {
                            requested_retry = Some(job_id);
                        }
                    });
                    if let Some(err) = job.error.as_deref() {
                        ui.weak(format!("  error: {err}"));
                    }
                    if let Some(sel) = self.selected_job {
                        if sel == job_id {
                            for event in &self.detail_events {
                                ui.label(
                                    egui::RichText::new(format!(
                                        "{} {}",
                                        event.ts,
                                        event.message.clone().unwrap_or_default()
                                    ))
                                    .small()
                                    .color(egui::Color32::from_rgb(190, 190, 200)),
                                );
                            }
                        }
                    }
                    ui.separator();
                }
            });
        if let Some(id) = requested_cancel {
            let _ = request_cancel(&self.root, id);
            self.selected_job = None;
        }
        if let Some(id) = requested_retry {
            let _ = retry_job(&self.root, id);
        }
    }

    fn panel_create(&mut self, ui: &mut egui::Ui) {
        ui.heading("Create Job");
        let mut submit = false;
        {
            let f = &mut self.form;
            ui.label("Topic");
            ui.text_edit_singleline(&mut f.topic);
            ui.label("Run mode");
            ui.text_edit_singleline(&mut f.run_mode);
            ui.label("Segment count");
            ui.text_edit_singleline(&mut f.segment_count);
            ui.label("Images per segment");
            ui.text_edit_singleline(&mut f.images_per_segment);
            ui.label("Target duration (seconds, optional)");
            ui.text_edit_singleline(&mut f.duration_minutes);
            ui.checkbox(&mut f.dry_run, "Dry run (plan only, no render)");
            ui.checkbox(&mut f.skip_preflight, "Skip preflight");
            if ui.button("Queue job").clicked() {
                submit = true;
            }
            if let Some(msg) = &f.submitted {
                ui.colored_label(egui::Color32::from_rgb(90, 200, 120), msg);
            }
            if let Some(err) = &f.error {
                ui.colored_label(egui::Color32::from_rgb(220, 120, 120), err);
            }
        }
        if submit {
            self.submit_job();
        }
    }

    fn panel_canvas(&mut self, ui: &mut egui::Ui) {
        ui.heading("Director Canvas");
        let Some(job) = self.canvas_job.clone() else {
            ui.weak("No job selected yet. Run one from Jobs or Create Job.");
            return;
        };
        ui.horizontal(|ui| {
            ui.label(format!(
                "#{} {}",
                job.id,
                job.topic.clone().unwrap_or_default()
            ));
            ui.label(egui::RichText::new(job.status.as_str()).color(status_color(&job.status)));
        });
        let Some(out) = job.output_path.clone() else {
            ui.weak("No output video on this job yet.");
            if !self.detail_events.is_empty() {
                ui.add_space(8.0);
                ui.collapsing("Events", |ui| {
                    for event in &self.detail_events {
                        ui.label(format!(
                            "{} {}",
                            event.ts,
                            event.message.clone().unwrap_or_default()
                        ));
                    }
                });
            }
            return;
        };
        ui.strong("Output");
        ui.monospace(&out);
        let path = Path::new(&out);
        ui.horizontal(|ui| {
            if ui.button("Open in Explorer").clicked() {
                open_in_explorer(path);
            }
            if ui.button("Play").clicked() {
                open_in_player(path);
            }
        });
        ui.separator();
        if !self.detail_events.is_empty() {
            ui.collapsing("Events", |ui| {
                for event in &self.detail_events {
                    ui.label(format!(
                        "{} {}",
                        event.ts,
                        event.message.clone().unwrap_or_default()
                    ));
                }
            });
        }
    }
}

/// Entry point invoked by `src/bin/videoai_gui.rs`.
pub fn run() -> Result<()> {
    let root = engine::resolve_root();
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_inner_size([1100.0, 720.0])
            .with_title("Video.AI Studio"),
        ..Default::default()
    };
    eframe::run_native(
        "Video.AI Studio",
        options,
        Box::new(move |_cc| Ok(Box::new(VideoApp::new(root.clone())))),
    )
    .map_err(|e| anyhow::anyhow!("failed to run native app: {e}"))
}

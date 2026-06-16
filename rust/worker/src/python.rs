use std::path::PathBuf;

use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;

use crate::audio::{analyze_path, master_path, AudioAnalyzeArgs, AudioMasterArgs};

fn py_err(err: impl std::fmt::Display) -> PyErr {
    PyRuntimeError::new_err(err.to_string())
}

#[pyfunction]
#[pyo3(signature = (input, expected_duration=None, requested_duration=None))]
fn analyze_audio_wave(
    input: String,
    expected_duration: Option<f64>,
    requested_duration: Option<f64>,
) -> PyResult<String> {
    let report = analyze_path(&AudioAnalyzeArgs {
        input: PathBuf::from(input),
        json: true,
        expected_duration,
        requested_duration,
    })
    .map_err(py_err)?;
    serde_json::to_string(&report).map_err(py_err)
}

#[pyfunction]
#[pyo3(signature = (input, output, ffmpeg_bin="ffmpeg".to_string()))]
fn master_audio(input: String, output: String, ffmpeg_bin: String) -> PyResult<String> {
    let report = master_path(&AudioMasterArgs {
        input: PathBuf::from(input),
        output: PathBuf::from(output),
        json: true,
        ffmpeg_bin: PathBuf::from(ffmpeg_bin),
    })
    .map_err(py_err)?;
    serde_json::to_string(&report).map_err(py_err)
}

#[pymodule]
fn videoai_worker_native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(analyze_audio_wave, module)?)?;
    module.add_function(wrap_pyfunction!(master_audio, module)?)?;
    Ok(())
}

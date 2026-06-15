use std::process::{Command, Output};

fn assert_success(output: Output) -> Output {
    assert!(
        output.status.success(),
        "command failed with status {:?}\nstdout:\n{}\nstderr:\n{}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    output
}

#[test]
fn checkpoint_subcommand_round_trips_and_clears_state() {
    let bin = env!("CARGO_BIN_EXE_videoai-worker");
    let temp = tempfile::tempdir().expect("tempdir should be created");

    assert_success(
        Command::new(bin)
            .arg("checkpoint")
            .arg("save")
            .arg("--dir")
            .arg(temp.path())
            .arg("--topic")
            .arg("CLI Topic")
            .arg("--step")
            .arg("render")
            .arg("--data-json")
            .arg(r#"{"ok":true,"path":"out.mp4"}"#)
            .output()
            .expect("checkpoint save should run"),
    );

    let get_output = assert_success(
        Command::new(bin)
            .arg("checkpoint")
            .arg("get")
            .arg("--dir")
            .arg(temp.path())
            .arg("--topic")
            .arg("CLI Topic")
            .output()
            .expect("checkpoint get should run"),
    );
    let get_json: serde_json::Value =
        serde_json::from_slice(&get_output.stdout).expect("get output should be JSON");
    assert_eq!(get_json["found"], true);
    assert_eq!(get_json["data"]["render"]["ok"], true);
    assert_eq!(get_json["data"]["render"]["path"], "out.mp4");

    assert_success(
        Command::new(bin)
            .arg("checkpoint")
            .arg("clear")
            .arg("--dir")
            .arg(temp.path())
            .arg("--topic")
            .arg("CLI Topic")
            .output()
            .expect("checkpoint clear should run"),
    );

    let missing_output = assert_success(
        Command::new(bin)
            .arg("checkpoint")
            .arg("get")
            .arg("--dir")
            .arg(temp.path())
            .arg("--topic")
            .arg("CLI Topic")
            .output()
            .expect("checkpoint get after clear should run"),
    );
    let missing_json: serde_json::Value =
        serde_json::from_slice(&missing_output.stdout).expect("missing output should be JSON");
    assert_eq!(missing_json["found"], false);
}

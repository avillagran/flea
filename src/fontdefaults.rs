// Preference changes run only after an explicit action in the selected-font viewer.
use std::process::{Command, Stdio};
pub fn run(verb: &str, target: &str, family: &str) -> Result<String, String> {
    let out = Command::new("timeout").args(["60", "python3", "-I", "-c", include_str!("fontdefaults.py"),
        verb, target, family]).stdin(Stdio::null()).output().map_err(|e| e.to_string())?;
    if !out.status.success() {
        return Err(String::from_utf8_lossy(&out.stderr).trim().to_string());
    }
    String::from_utf8(out.stdout).map_err(|_| "The font preferences reply is invalid.".into())
}

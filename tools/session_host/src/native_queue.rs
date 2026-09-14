//! Native queue adapter: no terminal input, no shell, no implicit retry.
use std::{path::PathBuf, process::{Command, Stdio}, time::{Duration, Instant}, io::Read};
#[derive(Clone)]
pub struct NativeQueue { pub executable: PathBuf, pub cwd: Option<PathBuf>, pub environment: Vec<(String,String)> }
pub fn is_thread_id(s: &str) -> bool {
    s.len()==36 && s.bytes().enumerate().all(|(i,b)| if [8,13,18,23].contains(&i) { b==b'-' } else { b.is_ascii_hexdigit() })
}
pub fn receipt(stdout: &str, thread: &str) -> Result<String,String> {
    let words: Vec<_> = stdout.split_whitespace().collect();
    if words.len()==6 && words[0]=="Queued" && words[1]=="message" && is_thread_id(words[2]) && words[3]=="for" && words[4]=="thread" && words[5]==format!("{thread}.") { Ok(words[2].into()) }
    else { Err("Native queue receipt did not match the exact bound thread; no retry".into()) }
}
impl NativeQueue {
    pub fn submit(&self, thread: &str, text: &str) -> Result<String,String> {
        let thread=thread.strip_prefix("codex:").unwrap_or(thread);
        if !is_thread_id(thread) || !self.executable.is_absolute() || !self.executable.is_file() || self.executable.extension().and_then(|v|v.to_str()).is_none_or(|v| !v.eq_ignore_ascii_case("exe")) {
            return Err("Native executable and exact bound thread UUID required".into());
        }
        // Windows argv has a 32767 UTF-16 unit limit. Never split one message.
        if text.encode_utf16().count()+self.executable.to_string_lossy().encode_utf16().count()+256 > 30000 {
            return Err("Native queue message exceeds Windows argument budget; no split or PTY fallback".into());
        }
        let mut command=Command::new(&self.executable);
        command.args(["queue","--thread",thread,"--message",text]).envs(self.environment.iter().cloned())
            .stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null());
        if let Some(cwd)=&self.cwd { command.current_dir(cwd); }
        #[cfg(windows)] { use std::os::windows::process::CommandExt; command.creation_flags(0x08000000); }
        let mut child=command.spawn().map_err(|e|format!("Native queue launch failed: {e}"))?;
        let start=Instant::now();
        loop {
            match child.try_wait() {
                Ok(Some(status)) => {
                    if !status.success() { return Err(format!("Native queue exited with {status}; no automatic retry")); }
                    let mut output=String::new();
                    child.stdout.take().ok_or("Native queue stdout missing")?.take(4096).read_to_string(&mut output).map_err(|e|e.to_string())?;
                    return receipt(&output,thread);
                }
                Err(error) => return Err(format!("Native queue wait failed: {error}")),
                _ => {}
            }
            if start.elapsed()>=Duration::from_secs(15) {
                let _=child.kill(); let _=child.wait();
                return Err("Native queue timed out; acceptance is unknown; no retry or PTY fallback".into());
            }
            std::thread::sleep(Duration::from_millis(25));
        }
    }
}
#[cfg(test)] mod tests {
 use super::*;
 #[test] fn receipt_requires_exact_thread_and_valid_queue_id() {
  let t="01a09ad0-7edc-75d0-8357-2dca0f074e8a";
  let q="01a09fb1-d574-7643-beaa-00d78db9e190";
  assert_eq!(receipt(&format!("Queued message {q} for thread {t}.\n"),t).unwrap(),q);
  assert!(receipt(&format!("Queued message {q} for thread {q}."),t).is_err());
  assert!(receipt(&format!("Queued message invalid for thread {t}."),t).is_err());
  assert!(!is_thread_id("session-name"));
 }
}

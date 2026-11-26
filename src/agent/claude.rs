use std::{
    collections::HashMap,
    fs,
    io::{self, BufRead, BufReader, Read, Write},
    path::Path,
    process::{Child, Command, ExitStatus, Stdio},
    thread,
};

use serde::{Deserialize, Serialize};
use tempfile::{NamedTempFile, TempDir};
use uuid::Uuid;

use crate::agent::{Agent, AgentError, Effort, ModelUsage, Response, RunError};

const DEFAULT_MODEL: &str = "opus";
const SANDBOX_SETTINGS: &str = r#"{"sandbox": {"enabled": true, "failIfUnavailable": true, "allowUnsandboxedCommands": false}, "permissions": {"blockWritesOutsideWorkingDirectories": true}}"#;

#[derive(Serialize, Deserialize)]
pub struct ClaudeCode {
    model: String,
    effort: Effort,
}

impl Agent for ClaudeCode {
    fn run(
        &mut self,
        req: &super::Request,
        hook: &mut dyn FnMut(&str),
    ) -> Result<super::Response, RunError> {
        let session_id = match req.session_id {
            Some(sid) => sid.clone(),
            None => {
                let sid = Uuid::new_v4().to_string();
                log::debug!("claude session {sid}");
                sid
            }
        };
        let mut briefing_file = None;
        if let (false, Some(briefing)) = (req.resume, req.briefing) {
            let mut file = NamedTempFile::new()?;
            file.write_all(briefing.as_bytes())?;
            briefing_file = Some(file);
        }
        let mut plugin_dir = None;
        if !req.skills.is_empty() {
            let dir = TempDir::new()?;
            write_plugin(dir.path(), req.skills)?;
            plugin_dir = Some(dir);
        }
        // The temporary directory of a sandboxed run: `claude` sets `$TMPDIR`
        // of Bash below it, and the file tools may write to it.
        //
        // It is in `/tmp`: `claude` does not use a `CLAUDE_CODE_TMPDIR` in the
        // system temporary directory of macOS (`/var/folders/…`).
        let mut scratch_dir = None;
        if req.options.sandbox {
            let dir = tempfile::Builder::new()
                .prefix("lusterna-")
                .tempdir_in("/tmp")?;
            let path = fs::canonicalize(dir.path())?;
            scratch_dir = Some((dir, path));
        }
        let args = self.args(
            req,
            &session_id,
            briefing_file.as_ref().map(NamedTempFile::path),
            plugin_dir.as_ref().map(TempDir::path),
            scratch_dir.as_ref().map(|(_, path)| path.as_path()),
        );
        let mut cmnd = Command::new("claude");
        cmnd.args(args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        if let Some(dir) = req.workdir {
            cmnd.current_dir(dir);
        }
        cmnd.envs(req.options.env.iter().map(|(k, v)| (k, v)));
        if let Some((_, path)) = &scratch_dir {
            cmnd.env("CLAUDE_CODE_TMPDIR", path);
        }
        let cmnd = cmnd.spawn()?;
        let output = read_output(cmnd, req.prompt.to_owned(), hook);
        drop(briefing_file);
        drop(plugin_dir);
        drop(scratch_dir);

        let (status, stderr, result) = output.map_err(|e| RunError {
            session_id: Some(session_id.clone()),
            usage: None,
            kind: e.into(),
        })?;
        let usage = result.as_ref().and_then(total_usage);
        match result_output(status, stderr, result, req.json_schema.is_some()) {
            Ok(output) => Ok(Response {
                session_id,
                output,
                usage: usage.unwrap_or_default(),
            }),
            Err(kind) => Err(RunError {
                session_id: Some(session_id),
                usage,
                kind,
            }),
        }
    }

    fn new_session_id(&self) -> Option<String> {
        Some(Uuid::new_v4().to_string())
    }

    fn set_effort(&mut self, effort: super::Effort) {
        self.effort = effort;
    }

    fn set_model(&mut self, model: String) {
        self.model = model
    }
}

fn read_output(
    mut cmnd: Child,
    prompt: String,
    hook: &mut dyn FnMut(&str),
) -> io::Result<(ExitStatus, String, Option<serde_json::Value>)> {
    let mut stdin = cmnd.stdin.take().expect("stdin is piped");
    let stdin = thread::spawn(move || stdin.write_all(prompt.as_bytes()));

    let mut stderr = cmnd.stderr.take().expect("stderr is piped");
    let stderr = thread::spawn(move || {
        let mut s = String::new();
        stderr.read_to_string(&mut s).map(|_| s)
    });

    let mut result = None;
    let stdout = cmnd.stdout.take().expect("stdout is piped");
    for line in BufReader::new(stdout).lines() {
        let Ok(event) = serde_json::from_str::<serde_json::Value>(&line?) else {
            continue;
        };
        if let Some(summary) = hook_summary(&event) {
            hook(&summary);
        }
        if event["type"] == "result" {
            result = Some(event);
        }
    }

    let status = cmnd.wait()?;
    stdin.join().expect("stdin writer panicked")?;
    let stderr = stderr.join().expect("stderr reader panicked")?;
    Ok((status, stderr, result))
}

fn write_plugin(dir: &Path, skills: &[(String, String)]) -> io::Result<()> {
    fs::create_dir(dir.join(".claude-plugin"))?;
    fs::write(
        dir.join(".claude-plugin").join("plugin.json"),
        r#"{"name": "lusterna"}"#,
    )?;
    for (name, content) in skills {
        let skill_dir = dir.join("skills").join(name);
        fs::create_dir_all(&skill_dir)?;
        fs::write(skill_dir.join("SKILL.md"), content)?;
    }
    Ok(())
}

fn hook_summary(event: &serde_json::Value) -> Option<String> {
    let summary = summarize(event)?;
    if event["parent_tool_use_id"].is_string() {
        log::trace!("subagent: {summary}");
        return None;
    }
    Some(summary)
}

fn total_usage(event: &serde_json::Value) -> Option<ModelUsage> {
    let per_model =
        serde_json::from_value::<HashMap<String, ModelUsage>>(event["modelUsage"].clone()).ok()?;
    let mut usage = ModelUsage::default();
    // `modelUsage` is per model, so we aggregate here
    for u in per_model.values() {
        usage.input_tokens += u.input_tokens;
        usage.output_tokens += u.output_tokens;
        usage.cache_read_input_tokens += u.cache_read_input_tokens;
        usage.cache_creation_input_tokens += u.cache_creation_input_tokens;
        usage.cost_usd += u.cost_usd;
    }
    Some(usage)
}

fn result_output(
    status: ExitStatus,
    stderr: String,
    result: Option<serde_json::Value>,
    structured: bool,
) -> Result<String, AgentError> {
    if !status.success() {
        if result
            .as_ref()
            .is_some_and(|e| e["subtype"] == "error_max_budget_usd")
        {
            return Err(AgentError::BudgetExhausted);
        }
        return Err(AgentError::Failed { status, stderr });
    }
    let event = result.ok_or(AgentError::NoResult { stderr })?;
    let text = event["result"].as_str().unwrap_or_default().to_owned();
    if event["is_error"] == true {
        return Err(AgentError::ResultError { result: text });
    }
    if structured {
        if event["structured_output"].is_null() {
            return Err(AgentError::NoStructuredOutput);
        }
        return Ok(event["structured_output"].to_string());
    }
    Ok(text)
}

impl ClaudeCode {
    fn args(
        &self,
        req: &super::Request,
        session_id: &str,
        briefing_file: Option<&Path>,
        plugin_dir: Option<&Path>,
        scratch_dir: Option<&Path>,
    ) -> Vec<String> {
        let mut args: Vec<String> = vec!["-p".into()];
        if req.resume {
            args.extend(["--resume".into(), session_id.to_owned()]);
        } else {
            args.extend(["--session-id".into(), session_id.to_owned()]);
        }
        if let Some(file) = briefing_file {
            args.extend([
                "--append-system-prompt-file".into(),
                file.to_string_lossy().into_owned(),
            ]);
        }
        args.extend([
            "--output-format".into(),
            "stream-json".into(),
            "--verbose".into(),
            "--permission-mode".into(),
            "dontAsk".into(),
            "--allowedTools".into(),
            req.options.allowed_tools.join(","),
            "--disallowedTools".into(),
            req.options.disallowed_tools.join(","),
            "--model".into(),
            self.model.clone(),
            "--setting-sources".into(),
            "project,local".into(),
        ]);

        args.extend(["--effort".to_owned(), self.effort.to_string()]);

        if let Some(budget) = req.budget_limit_usd {
            args.extend(["--max-budget-usd".to_owned(), budget.to_string()]);
        }
        if let Some(schema) = req.json_schema {
            args.extend(["--json-schema".to_owned(), schema.to_string()]);
        }
        if let Some(dir) = plugin_dir {
            args.extend(["--plugin-dir".into(), dir.to_string_lossy().into_owned()]);
        }
        if let Some(dir) = scratch_dir {
            args.extend([
                "--add-dir".into(),
                dir.to_string_lossy().into_owned(),
                "--settings".into(),
                SANDBOX_SETTINGS.into(),
            ]);
        }
        args
    }
}

impl Default for ClaudeCode {
    fn default() -> Self {
        Self {
            model: DEFAULT_MODEL.to_string(),
            effort: Effort::default(),
        }
    }
}

fn summarize(event: &serde_json::Value) -> Option<String> {
    let short = |s: &str| {
        let line = s.split_whitespace().collect::<Vec<_>>().join(" ");
        match line.char_indices().nth(200) {
            Some((i, _)) => format!("{}...", &line[..i]),
            None => line,
        }
    };
    if event["type"] != "assistant" {
        return None;
    }
    let parts: Vec<String> = event["message"]["content"]
        .as_array()?
        .iter()
        .filter_map(|b| match b["type"].as_str()? {
            "text" => Some(b["text"].as_str()?.trim().to_owned()),
            "tool_use" => {
                let input = &b["input"];
                let what = input["description"]
                    .as_str()
                    .or(input["command"].as_str())
                    .or(input["file_path"]
                        .as_str()
                        .map(|p| p.rsplit('/').next().unwrap_or(p)))
                    .or(input["pattern"].as_str())
                    .or(input["skill"].as_str())
                    .unwrap_or("");
                Some(format!(
                    "{}: {}",
                    b["name"].as_str().unwrap_or(""),
                    short(what)
                ))
            }
            _ => None,
        })
        .collect();
    (!parts.is_empty()).then(|| parts.join(" | "))
}

#[cfg(all(test, unix))]
mod tests {
    use std::os::unix::process::ExitStatusExt;

    use serde_json::json;

    use super::*;
    use crate::agent::{AgentOptions, Request};

    fn request(options: &AgentOptions) -> Request<'_> {
        Request {
            prompt: "",
            briefing: None,
            json_schema: None,
            session_id: None,
            resume: false,
            budget_limit_usd: None,
            skills: &[],
            options,
            workdir: None,
        }
    }

    /// The value that follows the flag `name` in `args`
    fn flag<'a>(args: &'a [String], name: &str) -> Option<&'a str> {
        let i = args.iter().position(|a| a == name)?;
        args.get(i + 1).map(String::as_str)
    }

    fn exit(code: i32) -> ExitStatus {
        ExitStatus::from_raw(code << 8)
    }

    fn tool_use(input: serde_json::Value) -> serde_json::Value {
        json!({
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": "Bash", "input": input}]}
        })
    }

    #[test]
    fn args_of_a_new_session_set_the_session_id_and_the_briefing() {
        let options = AgentOptions::default();
        let args = ClaudeCode::default().args(
            &request(&options),
            "sid",
            Some(Path::new("/brief")),
            None,
            None,
        );
        assert_eq!(flag(&args, "--session-id"), Some("sid"));
        assert_eq!(flag(&args, "--append-system-prompt-file"), Some("/brief"));
        assert_eq!(flag(&args, "--resume"), None);
    }

    #[test]
    fn args_of_a_resumed_session_resume_it() {
        let options = AgentOptions::default();
        let sid = "sid".to_owned();
        let req = Request {
            session_id: Some(&sid),
            resume: true,
            ..request(&options)
        };
        let args = ClaudeCode::default().args(&req, &sid, None, None, None);
        assert_eq!(flag(&args, "--resume"), Some("sid"));
        assert_eq!(flag(&args, "--session-id"), None);
        assert_eq!(flag(&args, "--append-system-prompt-file"), None);
    }

    #[test]
    fn args_of_a_new_session_with_a_given_id_start_it() {
        let options = AgentOptions::default();
        let sid = "sid".to_owned();
        let req = Request {
            session_id: Some(&sid),
            ..request(&options)
        };
        let args = ClaudeCode::default().args(&req, &sid, None, None, None);
        assert_eq!(flag(&args, "--session-id"), Some("sid"));
        assert_eq!(flag(&args, "--resume"), None);
    }

    #[test]
    fn new_session_ids_differ() {
        let agent = ClaudeCode::default();
        assert!(agent.new_session_id().is_some());
        assert_ne!(agent.new_session_id(), agent.new_session_id());
    }

    #[test]
    fn args_set_the_budget_and_the_schema_only_when_given() {
        let options = AgentOptions::default();
        let agent = ClaudeCode::default();

        let args = agent.args(&request(&options), "sid", None, None, None);
        assert_eq!(flag(&args, "--model"), Some("opus"));
        assert_eq!(flag(&args, "--effort"), Some("medium"));
        assert_eq!(flag(&args, "--max-budget-usd"), None);
        assert_eq!(flag(&args, "--json-schema"), None);

        let req = Request {
            budget_limit_usd: Some(2.5),
            json_schema: Some("{}"),
            ..request(&options)
        };
        let args = agent.args(&req, "sid", None, None, None);
        assert_eq!(flag(&args, "--max-budget-usd"), Some("2.5"));
        assert_eq!(flag(&args, "--json-schema"), Some("{}"));
    }

    #[test]
    fn args_pass_the_tool_lists() {
        let options = AgentOptions {
            allowed_tools: vec!["Read".to_owned(), "Edit".to_owned()],
            disallowed_tools: vec!["Bash(lean *)".to_owned()],
            ..AgentOptions::default()
        };
        let args = ClaudeCode::default().args(&request(&options), "sid", None, None, None);
        assert_eq!(flag(&args, "--allowedTools"), Some("Read,Edit"));
        assert_eq!(flag(&args, "--disallowedTools"), Some("Bash(lean *)"));
    }

    #[test]
    fn args_add_the_plugin_dir_and_the_sandbox() {
        let options = AgentOptions::default();
        let args = ClaudeCode::default().args(
            &request(&options),
            "sid",
            None,
            Some(Path::new("/plugin")),
            Some(Path::new("/tmp/scratch")),
        );
        assert_eq!(flag(&args, "--plugin-dir"), Some("/plugin"));
        assert_eq!(flag(&args, "--add-dir"), Some("/tmp/scratch"));
        assert_eq!(flag(&args, "--settings"), Some(SANDBOX_SETTINGS));
    }

    #[test]
    fn write_plugin_writes_the_manifest_and_each_skill() {
        let dir = tempfile::tempdir().unwrap();
        let skills = [
            ("a".to_owned(), "skill a".to_owned()),
            ("b".to_owned(), "skill b".to_owned()),
        ];
        write_plugin(dir.path(), &skills).unwrap();

        let manifest = fs::read_to_string(dir.path().join(".claude-plugin/plugin.json")).unwrap();
        assert_eq!(manifest, r#"{"name": "lusterna"}"#);
        for (name, content) in &skills {
            let path = dir.path().join("skills").join(name).join("SKILL.md");
            assert_eq!(&fs::read_to_string(path).unwrap(), content);
        }
    }

    #[test]
    fn summarize_skips_events_without_text_or_tool_calls() {
        assert_eq!(summarize(&json!({"type": "result"})), None);
        assert_eq!(
            summarize(&json!({"type": "assistant", "message": {"content": []}})),
            None
        );
    }

    #[test]
    fn summarize_joins_text_and_tool_calls() {
        let event = json!({
            "type": "assistant",
            "message": {"content": [
                {"type": "text", "text": "  checking  "},
                {"type": "tool_use", "name": "Read", "input": {"file_path": "/a/b/lib.rs"}}
            ]}
        });
        assert_eq!(
            summarize(&event).as_deref(),
            Some("checking | Read: lib.rs")
        );
    }

    #[test]
    fn summarize_prefers_the_description_of_a_tool_call() {
        let event = tool_use(json!({"description": "build", "command": "lake build"}));
        assert_eq!(summarize(&event).as_deref(), Some("Bash: build"));
    }

    #[test]
    fn summarize_shortens_a_tool_call_to_one_line_of_200_chars() {
        let event = tool_use(json!({"command": format!("a\n  b {}", "é".repeat(300))}));
        let expected = format!("Bash: a b {}...", "é".repeat(196));
        assert_eq!(summarize(&event), Some(expected));
    }

    #[test]
    fn hook_summary_skips_subagent_events() {
        let mut event = tool_use(json!({"command": "ls"}));
        assert_eq!(hook_summary(&event).as_deref(), Some("Bash: ls"));
        event["parent_tool_use_id"] = json!("toolu_1");
        assert_eq!(hook_summary(&event), None);
    }

    #[test]
    fn total_usage_sums_over_the_models() {
        let event = json!({"modelUsage": {
            "opus": {"inputTokens": 1, "outputTokens": 2, "cacheReadInputTokens": 3,
                     "cacheCreationInputTokens": 4, "costUSD": 0.5},
            "haiku": {"inputTokens": 10, "outputTokens": 20, "cacheReadInputTokens": 30,
                      "cacheCreationInputTokens": 40, "costUSD": 0.25}
        }});
        let usage = total_usage(&event).unwrap();
        assert_eq!(usage.input_tokens, 11);
        assert_eq!(usage.output_tokens, 22);
        assert_eq!(usage.cache_read_input_tokens, 33);
        assert_eq!(usage.cache_creation_input_tokens, 44);
        assert_eq!(usage.cost_usd, 0.75);
    }

    #[test]
    fn total_usage_of_an_event_without_usage_is_none() {
        assert!(total_usage(&json!({"type": "result"})).is_none());
    }

    #[test]
    fn result_output_returns_the_final_message() {
        let event = json!({"type": "result", "result": "done"});
        let output = result_output(exit(0), String::new(), Some(event), false).unwrap();
        assert_eq!(output, "done");
    }

    #[test]
    fn result_output_returns_the_structured_output_as_json() {
        let event = json!({"type": "result", "result": "", "structured_output": {"a": 1}});
        let output = result_output(exit(0), String::new(), Some(event), true).unwrap();
        assert_eq!(output, r#"{"a":1}"#);
    }

    #[test]
    fn result_output_without_structured_output_fails() {
        let event = json!({"type": "result", "result": "done"});
        let err = result_output(exit(0), String::new(), Some(event), true).unwrap_err();
        assert!(matches!(err, AgentError::NoStructuredOutput));
    }

    #[test]
    fn result_output_of_an_error_result_fails() {
        let event = json!({"type": "result", "result": "oops", "is_error": true});
        let err = result_output(exit(0), String::new(), Some(event), false).unwrap_err();
        assert!(matches!(err, AgentError::ResultError { result } if result == "oops"));
    }

    #[test]
    fn result_output_without_result_event_fails() {
        let err = result_output(exit(0), "stderr".to_owned(), None, false).unwrap_err();
        assert!(matches!(err, AgentError::NoResult { stderr } if stderr == "stderr"));
    }

    #[test]
    fn result_output_of_an_exhausted_budget_fails() {
        let event = json!({"type": "result", "subtype": "error_max_budget_usd"});
        let err = result_output(exit(1), String::new(), Some(event), false).unwrap_err();
        assert!(matches!(err, AgentError::BudgetExhausted));
    }

    #[test]
    fn result_output_of_a_failed_run_fails() {
        let event = json!({"type": "result", "subtype": "error_during_execution"});
        let err = result_output(exit(1), "stderr".to_owned(), Some(event), false).unwrap_err();
        assert!(matches!(err, AgentError::Failed { stderr, .. } if stderr == "stderr"));
    }
}

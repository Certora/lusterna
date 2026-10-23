use std::{
    collections::HashMap,
    fmt::Display,
    fs,
    io::{self, BufRead, BufReader, Write},
    process::{Command, ExitStatus, Stdio},
    thread,
};

use serde::Deserialize;
use std::io::Read;
use tempfile::{NamedTempFile, TempDir};
use uuid::Uuid;

const DEFAULT_ALLOWED_TOOLS: &[&str] =
    &["Bash", "Edit", "Write", "Read", "Glob", "Grep", "TodoWrite"];

const DEFAULT_MODEL: &str = "opus";

/// The effort level for the agent
#[derive(Debug, Clone, Copy, Default, clap::ValueEnum)]
pub enum Effort {
    Low,
    Medium,
    #[default]
    High,
    #[value(name = "xhigh")]
    XHigh,
    Max,
}

impl Display for Effort {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Effort::Low => write!(f, "low"),
            Effort::Medium => write!(f, "medium"),
            Effort::High => write!(f, "high"),
            Effort::XHigh => write!(f, "xhigh"),
            Effort::Max => write!(f, "max"),
        }
    }
}

#[derive(Debug, Default, Deserialize)]
#[serde(default, rename_all = "camelCase")]
pub struct ModelUsage {
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub cache_read_input_tokens: u64,
    pub cache_creation_input_tokens: u64,
    #[serde(rename = "costUSD")]
    pub cost_usd: f64,
}

#[derive(Debug)]
pub struct Agent {
    briefing: Option<String>,
    allowed_tools: Vec<String>,
    effort: Option<Effort>,
    max_budged: Option<f64>,
    model: String,
    usage: ModelUsage,
    session_id: Option<Uuid>,
    json_schema: Option<String>,
    /// The name and the `SKILL.md` content of each skill
    skills: Vec<(String, String)>,
}

impl Default for Agent {
    fn default() -> Self {
        Self {
            briefing: None,
            allowed_tools: DEFAULT_ALLOWED_TOOLS
                .iter()
                .map(|s| s.to_string())
                .collect(),
            effort: Default::default(),
            max_budged: None,
            usage: Default::default(),
            model: DEFAULT_MODEL.to_string(),
            session_id: None,
            json_schema: None,
            skills: Vec::new(),
        }
    }
}

#[derive(Debug, thiserror::Error)]
pub enum AgentError {
    #[error("IO: {0}")]
    Io(#[from] io::Error),
    #[error("`claude` failed with {status}:\n{stderr}")]
    Failed { status: ExitStatus, stderr: String },
    #[error("`claude` printed no result event:\n{stderr}")]
    NoResult { stderr: String },
    #[error("the agent run ended with an error: {result}")]
    ResultError { result: String },
    #[error("JSON: {0}")]
    Json(#[from] serde_json::Error),
}

impl Agent {
    pub fn set_briefing(&mut self, briefing: String) {
        self.briefing = Some(briefing)
    }

    pub fn set_max_budget(&mut self, max_budged: f64) {
        self.max_budged = Some(max_budged)
    }

    pub fn set_effort(&mut self, effort: Effort) {
        self.effort = Some(effort)
    }

    pub fn set_model(&mut self, model: String) {
        self.model = model
    }

    /// Makes `run` return the agent's structured output, as JSON matching
    /// `schema`, instead of its final message.
    pub fn set_json_schema(&mut self, schema: String) {
        self.json_schema = Some(schema)
    }

    /// Makes the skill `name` with the `SKILL.md` content `content` available
    /// to the agent as `lusterna:<name>`.
    pub fn add_skill(&mut self, name: &str, content: String) {
        if !self.allowed_tools.iter().any(|t| t == "Skill") {
            self.allowed_tools.push("Skill".to_owned());
        }
        self.skills.push((name.to_owned(), content))
    }

    pub fn get_usage(&self) -> &ModelUsage {
        &self.usage
    }

    /// Return the agents final message
    pub fn run(&mut self, prompt: String) -> Result<String, AgentError> {
        self.run_with_hook(prompt, |_| {})
    }

    pub fn run_with_hook(
        &mut self,
        prompt: String,
        mut hook: impl FnMut(&str),
    ) -> Result<String, AgentError> {
        let mut args: Vec<String> = vec!["-p".into()];
        let mut briefing_file = None;
        if let Some(r_sid) = self.session_id {
            args.extend(["--resume".into(), r_sid.to_string()]);
        } else {
            let sid = Uuid::new_v4();
            self.session_id = Some(sid);

            args.extend(["--session-id".into(), sid.to_string().clone()]);
            if let Some(briefing) = self.briefing.as_ref() {
                let mut file = NamedTempFile::new()?;
                file.write_all(briefing.as_bytes())?;
                args.extend([
                    "--append-system-prompt-file".into(),
                    file.path().to_string_lossy().into_owned(),
                ]);
                briefing_file = Some(file);
            }
        }
        args.extend([
            "--output-format".into(),
            "stream-json".into(),
            "--verbose".into(),
            "--permission-mode".into(),
            "dontAsk".into(),
            "--allowedTools".into(),
            self.allowed_tools.join(","),
            "--model".into(),
            self.model.clone(),
        ]);
        if let Some(effort) = self.effort {
            args.extend(["--effort".to_owned(), effort.to_string()]);
        }
        if let Some(budget) = self.max_budged {
            args.extend(["--max-budget-usd".to_owned(), budget.to_string()]);
        }
        if let Some(schema) = self.json_schema.as_ref() {
            args.extend(["--json-schema".to_owned(), schema.clone()]);
        }
        let mut plugin_dir = None;
        if !self.skills.is_empty() {
            let dir = TempDir::new()?;
            fs::create_dir(dir.path().join(".claude-plugin"))?;
            fs::write(
                dir.path().join(".claude-plugin").join("plugin.json"),
                r#"{"name": "lusterna"}"#,
            )?;
            for (name, content) in &self.skills {
                let skill_dir = dir.path().join("skills").join(name);
                fs::create_dir_all(&skill_dir)?;
                fs::write(skill_dir.join("SKILL.md"), content)?;
            }
            args.extend([
                "--plugin-dir".into(),
                dir.path().to_string_lossy().into_owned(),
            ]);
            plugin_dir = Some(dir);
        }
        let mut result = None;

        let mut cmnd = Command::new("claude")
            .args(args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()?;

        let mut stdin = cmnd.stdin.take().expect("stdin is piped");
        let stdin = thread::spawn(move || stdin.write_all(prompt.as_bytes()));

        let mut stderr = cmnd.stderr.take().expect("stderr is piped");
        let stderr = thread::spawn(move || {
            let mut s = String::new();
            stderr.read_to_string(&mut s).map(|_| s)
        });

        let stdout = cmnd.stdout.take().expect("stdout is piped");
        for line in BufReader::new(stdout).lines() {
            let Ok(event) = serde_json::from_str::<serde_json::Value>(&line?) else {
                continue;
            };
            if let Some(summary) = summarize(&event) {
                hook(&summary);
            }
            if event["type"] == "result" {
                result = Some(event);
            }
        }

        let status = cmnd.wait()?;
        stdin.join().expect("stdin writer panicked")?;
        let stderr = stderr.join().expect("stderr reader panicked")?;
        drop(briefing_file);
        drop(plugin_dir);

        if !status.success() {
            return Err(AgentError::Failed { status, stderr });
        }
        let event = result.ok_or(AgentError::NoResult { stderr })?;
        if let Ok(per_model) =
            serde_json::from_value::<HashMap<String, ModelUsage>>(event["modelUsage"].clone())
        {
            for u in per_model.values() {
                self.usage.input_tokens += u.input_tokens;
                self.usage.output_tokens += u.output_tokens;
                self.usage.cache_read_input_tokens += u.cache_read_input_tokens;
                self.usage.cache_creation_input_tokens += u.cache_creation_input_tokens;
                self.usage.cost_usd += u.cost_usd;
            }
        }
        let text = event["result"].as_str().unwrap_or_default().to_owned();
        if event["is_error"] == true {
            return Err(AgentError::ResultError { result: text });
        }
        if self.json_schema.is_some() {
            return Ok(event["structured_output"].to_string());
        }
        Ok(text)
    }
}

/// Returns a one-line summary of the text and tool calls in a `stream-json`
/// event of `claude`, or `None` if the event has neither.
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
                Some(short(what))
            }
            _ => None,
        })
        .collect();
    (!parts.is_empty()).then(|| parts.join(" | "))
}

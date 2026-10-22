use std::{
    collections::HashMap,
    io::{self, BufRead, BufReader, Write},
    process::{Command, ExitStatus, Stdio},
    thread,
};

use serde::Deserialize;
use std::io::Read;
use tempfile::NamedTempFile;
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

impl Effort {
    pub fn to_string(&self) -> String {
        match self {
            Effort::Low => "low".to_owned(),
            Effort::Medium => "medium".to_owned(),
            Effort::High => "high".to_owned(),
            Effort::XHigh => "xhigh".to_owned(),
            Effort::Max => "max".to_owned(),
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
        args.push(prompt);

        let mut result = None;

        let mut cmnd = Command::new("claude")
            .args(args)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()?;

        let mut stderr = cmnd.stderr.take().expect("stderr is piped");
        let stderr = thread::spawn(move || {
            let mut s = String::new();
            stderr.read_to_string(&mut s).map(|_| s)
        });

        let stdout = cmnd.stdout.take().expect("stdout is piped");
        for line in BufReader::new(stdout).lines() {
            let line = line?;
            hook(&line);
            if let Ok(event) = serde_json::from_str::<serde_json::Value>(&line)
                && event["type"] == "result"
            {
                result = Some(event);
            }
        }

        let status = cmnd.wait()?;
        let stderr = stderr.join().expect("stderr reader panicked")?;
        drop(briefing_file);

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

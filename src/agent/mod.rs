pub(crate) mod claude;
#[cfg(test)]
mod mock;

use std::{
    fmt::Display,
    io::{self},
    path::{Path, PathBuf},
    process::ExitStatus,
};

use serde::{Deserialize, Serialize};

use crate::{agent::claude::ClaudeCode, config::ConfigError};

const DEFAULT_ALLOWED_TOOLS: &[&str] =
    &["Bash", "Edit", "Write", "Read", "Glob", "Grep", "TodoWrite"];

const DISALLOWED_TOOLS: &[&str] = &["ScheduleWakeup"];

type SessionId = String;

#[derive(Debug, Default, Deserialize, Serialize, Clone)]
#[serde(default, rename_all = "camelCase")]
pub struct ModelUsage {
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub cache_read_input_tokens: u64,
    pub cache_creation_input_tokens: u64,
    #[serde(rename = "costUSD")]
    pub cost_usd: f64,
}

#[derive(Debug, thiserror::Error)]
pub enum AgentError {
    #[error("IO: {0}")]
    Io(#[from] io::Error),
    #[error("`claude` failed with {status}:\n{stderr}")]
    Failed { status: ExitStatus, stderr: String },
    #[error("`claude` reached its maximum budget")]
    BudgetExhausted,
    #[error("`claude` printed no result event:\n{stderr}")]
    NoResult { stderr: String },
    #[error("the agent run ended with an error: {result}")]
    ResultError { result: String },
    #[error("the agent run ended without a structured output")]
    NoStructuredOutput,
    #[error("JSON: {0}")]
    Json(#[from] serde_json::Error),
    #[error(transparent)]
    Config(#[from] ConfigError),
    #[error("the agent failed to often, giving up")]
    MaxRetries,
}

impl AgentError {
    const SUBMIT_STRUCTURED_OUTPUT: &str =
        "You stopped without submitting your structured output. Submit it now.";

    fn retry_prompt(&self) -> Option<String> {
        match self {
            AgentError::NoStructuredOutput => Some(Self::SUBMIT_STRUCTURED_OUTPUT.to_string()),
            _ => None,
        }
    }
}

pub struct Request<'a> {
    /// The prompt
    pub prompt: &'a str,

    /// The briefing (aka system prompt) to use
    pub briefing: Option<&'a str>,

    /// The json schema to be used for structured output
    pub json_schema: Option<&'a str>,

    /// The session to use or `None` if a new session should be spawned
    pub session_id: Option<&'a SessionId>,

    /// Maximum budget to use
    pub budget_limit_usd: Option<f64>,

    /// The name and the `SKILL.md` content of each skill
    pub skills: &'a [(String, String)],

    pub options: &'a AgentOptions,

    /// The dir to work in or `None` if the current workdir should be used
    pub workdir: Option<&'a Path>,
}

/// Response produced by the agent
pub struct Response {
    /// The if of the session that emitted the response
    pub session_id: String,
    /// The final message, or the structured output as JSON
    pub output: String,
    /// Usage stats
    pub usage: ModelUsage,
}

pub struct RunError {
    pub session_id: Option<SessionId>,
    pub usage: Option<ModelUsage>,
    pub kind: AgentError,
}

impl From<io::Error> for RunError {
    fn from(e: io::Error) -> Self {
        RunError {
            session_id: None,
            usage: None,
            kind: e.into(),
        }
    }
}

pub trait Agent: Default {
    fn run(&mut self, req: &Request, hook: &mut dyn FnMut(&str)) -> Result<Response, RunError>;

    /// Sets the effort of the agent
    fn set_effort(&mut self, effort: Effort);

    /// Sets the model of the agent, identified by its name.
    fn set_model(&mut self, model: String);
}

#[derive(Debug, Serialize, Deserialize, Default)]
pub struct Session<A: Agent = ClaudeCode> {
    agent: A,
    briefing: Option<String>,

    /// The id of the session or `None` if no prompt was issued yet.
    session_id: Option<SessionId>,

    /// The name and the `SKILL.md` content of each skill
    skills: Vec<(String, String)>,
    options: AgentOptions,

    /// The workdir in which to spawn the agent in or `None` if the current
    /// workdir should be used
    workdir: Option<PathBuf>,

    /// Maximum budget the session is allowed to consume
    max_budged: Option<f64>,

    usage: ModelUsage,
}

impl<A: Agent> Session<A> {
    const MAX_RETRIES: u8 = 5;

    pub fn get_session_id(&self) -> Option<&SessionId> {
        self.session_id.as_ref()
    }

    pub fn get_usage(&self) -> &ModelUsage {
        &self.usage
    }

    pub fn set_briefing(&mut self, briefing: String) {
        self.briefing = Some(briefing)
    }

    pub fn set_max_budget(&mut self, max_budged: f64) {
        self.max_budged = Some(max_budged)
    }

    pub fn set_dir(&mut self, dir: PathBuf) {
        self.workdir = Some(dir)
    }

    pub fn set_options(&mut self, options: AgentOptions) {
        self.options = options
    }

    pub fn set_effort(&mut self, effort: Effort) {
        self.agent.set_effort(effort)
    }

    pub fn set_model(&mut self, model: String) {
        self.agent.set_model(model)
    }

    /// Makes the skill `name` with the `SKILL.md` content `content` available
    /// to the agent as `lusterna:<name>`.
    pub fn add_skill(&mut self, name: &str, content: String) {
        if !self.options.allowed_tools.iter().any(|t| t == "Skill") {
            self.options.allowed_tools.push("Skill".to_owned());
        }
        self.skills.push((name.to_owned(), content))
    }

    pub fn run(
        &mut self,
        prompt: String,
        json_schema: Option<String>,
        hook: impl FnMut(&str),
    ) -> Result<Response, AgentError> {
        self.run_with_retry(prompt, json_schema, hook, 0)
    }

    fn run_with_retry(
        &mut self,
        prompt: String,
        json_schema: Option<String>,
        mut hook: impl FnMut(&str),
        retries: u8,
    ) -> Result<Response, AgentError> {
        if retries == Self::MAX_RETRIES {
            return Err(AgentError::MaxRetries);
        }
        let request = Request {
            prompt: &prompt,
            briefing: self.briefing.as_ref().map(|b| b.as_str()),
            session_id: self.session_id.as_ref(),
            budget_limit_usd: self.max_budged,
            skills: &self.skills,
            options: &self.options,
            workdir: self.workdir.as_ref().map(|p| p.as_path()),
            json_schema: json_schema.as_ref().map(|s| s.as_str()),
        };
        match self.agent.run(&request, &mut hook) {
            Ok(resp) => {
                if self.session_id.is_none() {
                    self.session_id = Some(resp.session_id.clone());
                }
                debug_assert!(self.session_id.as_ref() == Some(&resp.session_id));

                self.usage = resp.usage.clone();
                Ok(resp)
            }
            Err(err) => {
                if let Some(session_id) = err.session_id {
                    self.session_id = Some(session_id);
                }
                if let Some(usage) = err.usage {
                    self.usage = usage;
                }
                if let Some(retryprompt) = err.kind.retry_prompt() {
                    self.run_with_retry(retryprompt, json_schema, hook, retries + 1)
                } else {
                    Err(err.kind)
                }
            }
        }
    }
}

/// The tools and the environment of `claude`.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct AgentOptions {
    /// Tools that run without asking
    pub allowed_tools: Vec<String>,
    /// Tools and permission rules, such as `Bash(lean *)`, that are denied
    pub disallowed_tools: Vec<String>,
    /// Environment variables of `claude`
    pub env: Vec<(String, String)>,
    /// Confines the writes of Bash and of the file tools to the working
    /// directory and to a temporary directory that lasts for one run of
    /// `claude`, and blocks the network of Bash
    pub sandbox: bool,
}

impl Default for AgentOptions {
    fn default() -> Self {
        Self {
            allowed_tools: DEFAULT_ALLOWED_TOOLS
                .iter()
                .map(|s| s.to_string())
                .collect(),
            disallowed_tools: DISALLOWED_TOOLS.iter().map(|s| s.to_string()).collect(),
            env: vec![(
                "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS".to_owned(),
                "1".to_owned(),
            )],
            sandbox: false,
        }
    }
}

/// The effort level for the agent
#[derive(Debug, Clone, Copy, Default, clap::ValueEnum, Serialize, Deserialize)]
pub enum Effort {
    Low,
    #[default]
    Medium,
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

#[cfg(test)]
mod tests {
    use super::{
        mock::{MockAgent, Recorded},
        *,
    };

    fn usage(cost_usd: f64) -> ModelUsage {
        ModelUsage {
            cost_usd,
            ..ModelUsage::default()
        }
    }

    fn ok(session_id: &str, cost_usd: f64) -> Result<Response, RunError> {
        Ok(Response {
            session_id: session_id.to_owned(),
            output: "output".to_owned(),
            usage: usage(cost_usd),
        })
    }

    fn err(
        session_id: Option<&str>,
        cost_usd: Option<f64>,
        kind: AgentError,
    ) -> Result<Response, RunError> {
        Err(RunError {
            session_id: session_id.map(str::to_owned),
            usage: cost_usd.map(usage),
            kind,
        })
    }

    fn session(responses: Vec<Result<Response, RunError>>) -> Session<MockAgent> {
        Session {
            agent: MockAgent {
                responses: responses.into(),
                requests: Vec::new(),
            },
            ..Session::default()
        }
    }

    fn run(session: &mut Session<MockAgent>, prompt: &str) -> Result<Response, AgentError> {
        session.run(prompt.to_owned(), Some("{}".to_owned()), |_| {})
    }

    fn session_ids(session: &Session<MockAgent>) -> Vec<Option<&str>> {
        let requests = &session.agent.requests;
        requests.iter().map(|r| r.session_id.as_deref()).collect()
    }

    #[test]
    fn first_run_starts_a_session_and_later_runs_resume_it() {
        let mut session = session(vec![ok("s1", 1.0), ok("s1", 2.0)]);
        run(&mut session, "first").unwrap();
        run(&mut session, "second").unwrap();

        assert_eq!(session_ids(&session), [None, Some("s1")]);
        assert_eq!(session.get_session_id().map(String::as_str), Some("s1"));
    }

    /// One error of each `AgentError` variant. The match stops compiling when
    /// a variant is added.
    fn each_error() -> Vec<AgentError> {
        let errors = vec![
            AgentError::Io(io::Error::other("io")),
            AgentError::Failed {
                status: ExitStatus::default(),
                stderr: String::new(),
            },
            AgentError::BudgetExhausted,
            AgentError::NoResult {
                stderr: String::new(),
            },
            AgentError::ResultError {
                result: String::new(),
            },
            AgentError::NoStructuredOutput,
            AgentError::Json(serde_json::from_str::<()>("").unwrap_err()),
            AgentError::Config(ConfigError::NoCrateRoot(Vec::new())),
            AgentError::MaxRetries,
        ];
        // stops compiling when a new variant is added but not covered here.
        // ensures this is exhaustive.
        for error in &errors {
            match error {
                AgentError::Io(_)
                | AgentError::Failed { .. }
                | AgentError::BudgetExhausted
                | AgentError::NoResult { .. }
                | AgentError::ResultError { .. }
                | AgentError::NoStructuredOutput
                | AgentError::Json(_)
                | AgentError::Config(_)
                | AgentError::MaxRetries => {}
            }
        }
        errors
    }

    #[test]
    fn error_is_retried_in_its_session_only_with_a_retry_prompt() {
        for error in each_error() {
            let name = format!("{error:?}");
            let retry_prompt = error.retry_prompt();
            let mut session = session(vec![err(Some("s1"), None, error), ok("s1", 1.0)]);
            let result = run(&mut session, "work");

            let requests = &session.agent.requests;
            match retry_prompt {
                Some(prompt) => {
                    assert!(result.is_ok(), "{name}");
                    assert_eq!(requests.len(), 2, "{name}");
                    assert_eq!(requests[1].prompt, prompt, "{name}");
                    assert_eq!(requests[1].session_id.as_deref(), Some("s1"), "{name}");
                    assert_eq!(requests[1].json_schema.as_deref(), Some("{}"), "{name}");
                }
                None => {
                    assert!(result.is_err(), "{name}");
                    assert_eq!(requests.len(), 1, "{name}");
                }
            }
        }
    }

    #[test]
    fn failed_first_run_keeps_its_session() {
        let mut session = session(vec![
            err(
                Some("s1"),
                None,
                AgentError::ResultError {
                    result: "oops".to_owned(),
                },
            ),
            ok("s1", 1.0),
        ]);
        assert!(matches!(
            run(&mut session, "first"),
            Err(AgentError::ResultError { .. })
        ));

        run(&mut session, "second").unwrap();
        assert_eq!(session_ids(&session), [None, Some("s1")]);
    }

    #[test]
    fn run_that_failed_to_start_keeps_no_session() {
        let mut session = session(vec![
            err(None, None, io::Error::other("spawn").into()),
            ok("s1", 1.0),
        ]);
        assert!(matches!(run(&mut session, "first"), Err(AgentError::Io(_))));

        run(&mut session, "second").unwrap();
        assert_eq!(session_ids(&session), [None, None]);
    }

    #[test]
    fn each_run_replaces_the_usage() {
        let mut session = session(vec![
            ok("s1", 1.0),
            ok("s1", 3.0),
            err(Some("s1"), Some(4.0), AgentError::BudgetExhausted),
        ]);
        run(&mut session, "first").unwrap();
        assert_eq!(session.get_usage().cost_usd, 1.0);
        run(&mut session, "second").unwrap();
        assert_eq!(session.get_usage().cost_usd, 3.0);
        assert!(run(&mut session, "third").is_err());
        assert_eq!(session.get_usage().cost_usd, 4.0);
    }

    #[test]
    fn failed_run_without_usage_keeps_the_usage() {
        let mut session = session(vec![
            ok("s1", 1.0),
            err(
                Some("s1"),
                None,
                AgentError::NoResult {
                    stderr: String::new(),
                },
            ),
        ]);
        run(&mut session, "first").unwrap();
        assert!(run(&mut session, "second").is_err());
        assert_eq!(session.get_usage().cost_usd, 1.0);
    }

    #[test]
    fn retry_gives_up_after_max_retries() {
        let responses = (0..Session::<MockAgent>::MAX_RETRIES)
            .map(|_| err(Some("s1"), None, AgentError::NoStructuredOutput))
            .collect();
        let mut session = session(responses);

        assert!(matches!(
            run(&mut session, "work"),
            Err(AgentError::MaxRetries)
        ));
        assert_eq!(
            session.agent.requests.len(),
            usize::from(Session::<MockAgent>::MAX_RETRIES)
        );
    }

    #[test]
    fn request_carries_the_settings_of_the_session() {
        let mut session = session(vec![ok("s1", 1.0)]);
        session.set_briefing("briefing".to_owned());
        session.set_max_budget(2.5);
        session.set_dir(PathBuf::from("/work"));
        session.add_skill("skill", "content".to_owned());
        run(&mut session, "work").unwrap();

        assert_eq!(
            session.agent.requests,
            [Recorded {
                prompt: "work".to_owned(),
                session_id: None,
                json_schema: Some("{}".to_owned()),
                briefing: Some("briefing".to_owned()),
                budget_limit_usd: Some(2.5),
                skills: vec![("skill".to_owned(), "content".to_owned())],
                workdir: Some(PathBuf::from("/work")),
            }]
        );
    }

    #[test]
    fn add_skill_allows_the_skill_tool_once() {
        let mut session = session(Vec::new());
        session.add_skill("a", String::new());
        session.add_skill("b", String::new());

        let allowed = &session.options.allowed_tools;
        assert_eq!(allowed.iter().filter(|t| *t == "Skill").count(), 1);
        assert_eq!(session.skills.len(), 2);
    }
}

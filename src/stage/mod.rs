pub mod pre_translate;
pub mod translate;

use std::{
    error::Error,
    fmt::{self, Display},
    time::Instant,
};

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::{
    agent::{Agent, AgentError},
    config::Config,
    log::{stage_debug, stage_info},
    utils::format_elapsed,
};

pub trait Stage: Sized {
    type Input;

    type Output: Serialize;

    type SetupError: Error;

    /// The reason why the gate did not pass.
    /// The function `[feedback_prompt]` converts this type to a feedback prompt
    /// that is passed to the agent, so the agent can fix the failure.
    ///
    /// The `[GateExecutionError]` on the other hand is used to indicate
    /// something failed while running the gate checks. This is not passed
    /// back to agent and instead abort the stage.
    type GateFailure: Display;

    /// An error that can occur while running the gate.
    /// This is meant for errors that the agent can not fix, use `[GateFailure]`
    /// for this instead.
    type GateExecutionError: Error;

    // The stage's name.
    fn name() -> &'static str;

    /// Runs the stages setup phase.
    /// This phase is run before the agent is instructed to execute the stage.
    ///
    /// Runs (mechanical) check on the inputs before the stage itself is
    /// executed. "Mechanical" here means that they are deterministic checks
    /// and do **not** use an agent/LLM. If the check fails, the stage
    /// cannot be executed.
    fn setup(&mut self) -> Result<(), Self::SetupError>;

    /// Returns the base prompt of the agent
    fn base_prompt(&self) -> String;

    /// The stages briefing for the agent
    fn briefing(&self) -> String;

    /// The name and the `SKILL.md` content of each skill of the agent.
    /// By default, the agent has no skills.
    fn skills(&self) -> Vec<(&'static str, String)> {
        Vec::new()
    }

    /// Returns the feedback prompt for the agent for a failed gate check
    fn feedback_prompt(gate_error: &Self::GateFailure) -> String;

    /// The stage's gate checks. These check evaluate whether the agent that
    /// executes the stage worked correctly. If a gate check failed, then
    /// this is returned to the
    /// `agent_summary` is the agent's final message.
    fn gate_check(&self, agent_summary: &str) -> Result<Self::Output, GateStop<Self>>;

    /// The prompt for the judge, or `None` if the stage has no judge.
    /// By default, no judge is run.
    fn judge_prompt(&self, _output: &Self::Output, _agent_summary: &String) -> Option<String> {
        None
    }
}

/// Why a check stopped the gate.
pub enum GateStop<S: Stage> {
    Failure(S::GateFailure),
    Error(S::GateExecutionError),
}

impl<S: Stage> fmt::Debug for GateStop<S>
where
    S::GateFailure: fmt::Debug,
{
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            GateStop::Failure(e) => f.debug_tuple("Failure").field(e).finish(),
            GateStop::Error(e) => f.debug_tuple("Error").field(e).finish(),
        }
    }
}

#[derive(thiserror::Error)]
pub enum StageExecutionError<S: Stage> {
    #[error("setup failed: {0}")]
    SetupError(S::SetupError),
    #[error("failed to execute the gate: {0}")]
    GateExecutionError(S::GateExecutionError),
    #[error("max attempts reached{}", .0.as_ref().map(|r| format!(", {r}")).unwrap_or_default())]
    MaxRetries(Option<AttemptRejected<S>>),
    #[error("budget exhausted{}", .0.as_ref().map(|r| format!(", {r}")).unwrap_or_default())]
    BudgetExhausted(Option<AttemptRejected<S>>),
    #[error(transparent)]
    AgentError(#[from] AgentError),
}

impl<S: Stage> fmt::Debug for StageExecutionError<S> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        fmt::Display::fmt(self, f)
    }
}

pub enum AttemptRejected<S: Stage> {
    Gate(S::GateFailure),
    Judge(Vec<Defect>),
}

impl<S: Stage> fmt::Display for AttemptRejected<S> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            AttemptRejected::Gate(e) => write!(f, "last gate error: {e}"),
            AttemptRejected::Judge(defects) => {
                write!(f, "last judge defects:")?;
                for d in defects {
                    write!(f, "\n- {d}")?;
                }
                Ok(())
            }
        }
    }
}

pub fn run<S: Stage>(mut s: S, cfg: &Config) -> Result<S::Output, StageExecutionError<S>> {
    // Run the setup, if it fails, stop here.
    s.setup().map_err(StageExecutionError::SetupError)?;
    stage_info!(S::name(), "setup completed");

    let mut prompt: String = s.base_prompt();
    let mut last_rejection: Option<AttemptRejected<S>> = None;

    let mut agent = Agent::default();
    // set the briefing
    agent.set_briefing(s.briefing());
    for (name, content) in s.skills() {
        agent.add_skill(name, content);
    }
    agent.set_effort(cfg.effort);
    agent.set_model(cfg.model.clone());
    let mut judge_cost = 0.0;

    for attempt in 0..cfg.stage_max_attempts {
        let remaining = cfg.stage_max_budget as f64 - agent.get_usage().cost_usd - judge_cost;
        if remaining <= 0.0 {
            return Err(StageExecutionError::BudgetExhausted(last_rejection));
        }
        agent.set_max_budget(remaining);
        stage_info!(
            S::name(),
            "starting attempt {}/{}, remaining budget: {}/{} USD",
            attempt,
            cfg.stage_max_attempts,
            remaining,
            cfg.stage_max_budget
        );
        let t = Instant::now();
        let summary = match agent.run_with_hook(prompt, |s| stage_info!(S::name(), "agent: {}", s,))
        {
            Err(AgentError::BudgetExhausted) => {
                return Err(StageExecutionError::BudgetExhausted(last_rejection));
            }
            r => r?,
        };
        stage_info!(S::name(), "agent finished in {}", format_elapsed(t));
        stage_debug!(S::name(), "agent summary: {}", summary);
        match s.gate_check(&summary) {
            Ok(o) => {
                stage_info!(S::name(), "gate passed");
                match s.judge_prompt(&o, &summary) {
                    Some(judge_prompt) => {
                        stage_info!(S::name(), "running judge");
                        let t = Instant::now();
                        let remaining_budget =
                            cfg.stage_max_budget as f64 - agent.get_usage().cost_usd - judge_cost;
                        if remaining_budget <= 0.0 {
                            return Err(StageExecutionError::BudgetExhausted(last_rejection));
                        }
                        let (defects, cost) = run_judge::<S>(judge_prompt, cfg, remaining_budget)?;
                        judge_cost += cost;
                        stage_info!(S::name(), "judge finished in {}", format_elapsed(t));
                        if defects.is_empty() {
                            stage_info!(S::name(), "judge found no defects, continuing");
                            return Ok(o);
                        }
                        stage_info!(S::name(), "judge found defects");
                        stage_debug!(
                            S::name(),
                            "defects:\n{}",
                            (defects
                                .iter()
                                .map(|d| d.to_string())
                                .collect::<Vec<String>>()
                                .join("\n"))
                        );
                        prompt = defects_feedback_prompt(&defects);
                        last_rejection = Some(AttemptRejected::Judge(defects))
                    }
                    None => {
                        stage_info!(S::name(), "stage has no judge, continuing");
                        return Ok(o);
                    }
                }
            }
            Err(GateStop::Failure(e)) => {
                stage_info!(S::name(), "Gate failed: {}", e);
                prompt = S::feedback_prompt(&e);
                last_rejection = Some(AttemptRejected::Gate(e));
            }
            Err(GateStop::Error(e)) => return Err(StageExecutionError::GateExecutionError(e)),
        }
    }
    Err(StageExecutionError::MaxRetries(last_rejection))
}

/// A defect the judge found in a stage's output.
#[derive(Debug, Deserialize, JsonSchema)]
pub struct Defect {
    pub kind: String,
    pub detail: String,
    pub fix: String,
    pub theorem: Option<String>,
}

impl fmt::Display for Defect {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.kind, self.detail)
    }
}

/// Returns the feedback prompt for the agent for the defects the judge found
fn defects_feedback_prompt(defects: &[Defect]) -> String {
    let mut prompt = String::from("The judge found these defects, fix them:\n");
    for d in defects {
        let who = d.theorem.as_deref().unwrap_or(&d.kind);
        prompt.push_str(&format!(
            "- {} [{}]: {} -> fix: {}\n",
            who, d.kind, d.detail, d.fix
        ));
    }
    prompt
}

/// The judge's structured output. `--json-schema` requires an object at the
/// top level, so the defect list is wrapped in this struct. An empty `defects`
/// list approves the stage.
#[derive(Deserialize, JsonSchema)]
struct Verdict {
    defects: Vec<Defect>,
}

/// Returns the defects the judge found and the judge's cost in USD.
fn run_judge<S: Stage>(
    judge_prompt: String,
    cfg: &Config,
    max_budget: f64,
) -> Result<(Vec<Defect>, f64), AgentError> {
    let mut judge_agent = Agent::default();
    judge_agent.set_max_budget(max_budget);
    judge_agent.set_effort(cfg.effort);
    judge_agent.set_model(cfg.model.clone());
    // `claude --json-schema` only accepts draft-07 schemas. `schema_for!`
    // generates a draft 2020-12 schema, which it rejects.
    let schema = schemars::generate::SchemaSettings::draft07()
        .into_generator()
        .into_root_schema_for::<Verdict>();
    judge_agent.set_json_schema(serde_json::to_string(&schema)?);

    let judge_output =
        judge_agent.run_with_hook(judge_prompt, |s| stage_info!(S::name(), "judge: {}", s))?;
    let verdict: Verdict = serde_json::from_str(&judge_output)?;
    Ok((verdict.defects, judge_agent.get_usage().cost_usd))
}

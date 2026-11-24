use std::{
    collections::VecDeque,
    path::{Path, PathBuf},
};

use crate::agent::{Agent, Effort, Request, Response, RunError, SessionId};

/// The parts of a `Request` that the tests check
#[derive(Debug, PartialEq)]
pub struct Recorded {
    pub prompt: String,
    pub session_id: Option<SessionId>,
    pub json_schema: Option<String>,
    pub briefing: Option<String>,
    pub budget_limit_usd: Option<f64>,
    pub skills: Vec<(String, String)>,
    pub workdir: Option<PathBuf>,
}

/// Records each request and returns the next of `responses`
#[derive(Default)]
pub struct MockAgent {
    pub responses: VecDeque<Result<Response, RunError>>,
    pub requests: Vec<Recorded>,
}

impl Agent for MockAgent {
    fn run(&mut self, req: &Request, _hook: &mut dyn FnMut(&str)) -> Result<Response, RunError> {
        self.requests.push(Recorded {
            prompt: req.prompt.to_owned(),
            session_id: req.session_id.cloned(),
            json_schema: req.json_schema.map(str::to_owned),
            briefing: req.briefing.map(str::to_owned),
            budget_limit_usd: req.budget_limit_usd,
            skills: req.skills.to_vec(),
            workdir: req.workdir.map(Path::to_path_buf),
        });
        self.responses.pop_front().expect("a response for each run")
    }

    fn set_effort(&mut self, _effort: Effort) {}

    fn set_model(&mut self, _model: String) {}
}

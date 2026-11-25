use std::{
    cell::{Ref, RefCell},
    collections::VecDeque,
    path::{Path, PathBuf},
    rc::Rc,
};

use crate::agent::{Agent, Effort, Request, Response, RunError, SessionId};

/// The parts of a `Request` that the tests check
#[derive(Debug, PartialEq)]
pub struct Recorded {
    pub prompt: String,
    pub session_id: Option<SessionId>,
    pub resume: bool,
    pub json_schema: Option<String>,
    pub briefing: Option<String>,
    pub budget_limit_usd: Option<f64>,
    pub skills: Vec<(String, String)>,
    pub workdir: Option<PathBuf>,
}

/// Records each request and returns the next queued response. Clones share
/// the queue and the recorded requests.
#[derive(Clone, Default)]
pub struct MockAgent {
    responses: Rc<RefCell<VecDeque<Result<Response, RunError>>>>,
    requests: Rc<RefCell<Vec<Recorded>>>,
}

impl MockAgent {
    /// Queues `response` after the responses queued before
    pub fn push(&self, response: Result<Response, RunError>) {
        self.responses.borrow_mut().push_back(response);
    }

    pub fn requests(&self) -> Ref<'_, Vec<Recorded>> {
        self.requests.borrow()
    }
}

impl Agent for MockAgent {
    fn run(&mut self, req: &Request, _hook: &mut dyn FnMut(&str)) -> Result<Response, RunError> {
        self.requests.borrow_mut().push(Recorded {
            prompt: req.prompt.to_owned(),
            session_id: req.session_id.cloned(),
            resume: req.resume,
            json_schema: req.json_schema.map(str::to_owned),
            briefing: req.briefing.map(str::to_owned),
            budget_limit_usd: req.budget_limit_usd,
            skills: req.skills.to_vec(),
            workdir: req.workdir.map(Path::to_path_buf),
        });
        self.responses
            .borrow_mut()
            .pop_front()
            .expect("a response for each run")
    }

    fn new_session_id(&self) -> Option<SessionId> {
        None
    }

    fn set_effort(&mut self, _effort: Effort) {}

    fn set_model(&mut self, _model: String) {}
}

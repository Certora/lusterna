#[derive(Debug, Default)]
pub struct Agent {
    briefing: Option<String>,
    name: Option<String>,
}

#[derive(Debug, thiserror::Error)]
pub enum AgentError {}

impl Agent {
    pub fn with_name(name: String) -> Self {
        Self {
            name: Some(name),
            ..Default::default()
        }
    }

    pub fn run(&mut self, prompt: &str) -> Result<(), AgentError> {
        todo!()
    }
}

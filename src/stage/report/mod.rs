//! Renders the report of a pipeline run as Markdown from the submissions of
//! the stages, with the templates in `templates/`.

mod view;

use minijinja::{Environment, UndefinedBehavior, syntax::SyntaxConfig, value::Serde};

use crate::stage::{
    formalize::FormalizeSubmission, pre_translate::PreTranslateSubmission, properties::Properties,
    prove::ProveSubmission, translate::TranslateSubmission,
};

use view::ReportView;

/// The templates of the report, by name. `report.md` includes the others.
const TEMPLATES: [(&str, &str); 7] = [
    ("report.md", include_str!("templates/report.md")),
    ("summary.md", include_str!("templates/summary.md")),
    ("induction.md", include_str!("templates/induction.md")),
    ("property.md", include_str!("templates/property.md")),
    (
        "pre_translate.md",
        include_str!("templates/pre_translate.md"),
    ),
    ("translate.md", include_str!("templates/translate.md")),
    ("formalize.md", include_str!("templates/formalize.md")),
];

/// Returns the report of the crate `crate_name`. A stage submission is `None`
/// if the stage did not run.
pub fn render(
    crate_name: String,
    properties: &Properties,
    pre_translate: Option<PreTranslateSubmission>,
    translate: Option<TranslateSubmission>,
    formalize: Option<FormalizeSubmission>,
    prove: Option<&ProveSubmission>,
) -> Result<String, minijinja::Error> {
    let mut env = Environment::new();
    env.set_undefined_behavior(UndefinedBehavior::Strict);
    env.set_syntax(
        SyntaxConfig::builder()
            .trim_blocks(true)
            .lstrip_blocks(true)
            .keep_trailing_newline(true)
            .build()?,
    );
    for (name, source) in TEMPLATES {
        env.add_template(name, source)?;
    }

    let view = ReportView::new(
        crate_name,
        properties,
        pre_translate,
        translate,
        formalize,
        prove,
    );
    env.get_template("report.md")?.render(Serde(view))
}

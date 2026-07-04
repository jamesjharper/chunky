mod support;

use anyhow::Result;
use chunky_example::artifact_models::{
    ArtifactKey, ArtifactRequirement, ArtifactRole, PhaseMatchMode,
};
use chunky_example::stage_manifest::StageManifestBuilder;
use pretty_assertions::assert_eq;
use serde_json::json;
use std::collections::{BTreeMap, BTreeSet};
use support::*;

#[tokio::test]
async fn missing_optional_source_input_is_omitted_without_producer_probe() -> Result<()> {
    let in_a = decl("in_a", ArtifactRole::Source);
    let out_a = decl("out_a", ArtifactRole::Target);
    let stage = stage_registration(
        "stage_a",
        vec![in_a.artifact_id().clone()],
        vec![out_a.artifact_id().clone()],
        {
            let in_a = in_a.artifact_id().clone();
            move |_| vec![optional_requirement(in_a.clone())]
        },
    );
    let manifest = manifest(vec![in_a, out_a.clone()], vec![stage], BTreeMap::new());

    let plan = evaluate(
        &manifest,
        BTreeMap::new(),
        set([out_a.artifact_id().clone()]),
        BTreeSet::new(),
        None,
    )
    .await?;

    assert_eq!(1, plan.invocations.len());
    assert_eq!("stage_a", plan.invocations[0].registration.stage_id());
    assert!(plan.invocations[0].input_bindings.is_empty());
    Ok(())
}

#[tokio::test]
async fn two_stage_optional_path_is_pruned_when_required_source_is_missing() -> Result<()> {
    let in_a = decl("in_a", ArtifactRole::Source);
    let int_a = decl("int_a", ArtifactRole::Intermediate);
    let out_a = decl("out_a", ArtifactRole::Target);
    let stage_a = stage_registration(
        "stage_a",
        vec![in_a.artifact_id().clone()],
        vec![int_a.artifact_id().clone()],
        {
            let in_a = in_a.artifact_id().clone();
            move |_| vec![requirement(in_a.clone())]
        },
    );
    let stage_b = stage_registration(
        "stage_b",
        vec![int_a.artifact_id().clone()],
        vec![out_a.artifact_id().clone()],
        {
            let int_a = int_a.artifact_id().clone();
            move |_| vec![optional_requirement(int_a.clone())]
        },
    );
    let manifest = manifest(
        vec![in_a, int_a, out_a.clone()],
        vec![stage_a, stage_b],
        BTreeMap::new(),
    );

    let plan = evaluate(
        &manifest,
        BTreeMap::new(),
        set([out_a.artifact_id().clone()]),
        BTreeSet::new(),
        None,
    )
    .await?;

    assert_eq!(vec!["stage_b"], stage_ids(&plan));
    assert!(plan.invocations[0].input_bindings.is_empty());
    Ok(())
}

#[tokio::test]
async fn supplied_intermediate_stops_backward_walk_and_wins_over_producer_path() -> Result<()> {
    let in_a = decl("in_a", ArtifactRole::Source);
    let int_a = decl("int_a", ArtifactRole::Intermediate);
    let out_a = decl("out_a", ArtifactRole::Target);
    let stage_a = stage_registration(
        "stage_a",
        vec![in_a.artifact_id().clone()],
        vec![int_a.artifact_id().clone()],
        {
            let in_a = in_a.artifact_id().clone();
            move |_| vec![requirement(in_a.clone())]
        },
    );
    let stage_b = stage_registration(
        "stage_b",
        vec![int_a.artifact_id().clone()],
        vec![out_a.artifact_id().clone()],
        {
            let int_a = int_a.artifact_id().clone();
            move |_| vec![requirement(int_a.clone())]
        },
    );
    let manifest = manifest(
        vec![in_a, int_a.clone(), out_a.clone()],
        vec![stage_a, stage_b],
        BTreeMap::new(),
    );
    let mut supplied = BTreeMap::new();
    supplied.insert(
        int_a.artifact_id().clone(),
        source_instance(&int_a, json!({"semantic_id": "supplied-int-a"})),
    );

    let plan = evaluate(
        &manifest,
        supplied,
        set([out_a.artifact_id().clone()]),
        BTreeSet::new(),
        None,
    )
    .await?;

    assert_eq!(vec!["stage_b"], stage_ids(&plan));
    let input = &plan.invocations[0].input_bindings[0];
    assert_eq!(int_a.artifact_id(), &input.resolved_artifact_id);
    assert!(input.producer_invocation_key.is_none());
    Ok(())
}

#[tokio::test]
async fn supplied_intermediate_does_not_prune_shared_producer_needed_for_other_output() -> Result<()>
{
    let in_a = decl("in_a", ArtifactRole::Source);
    let int_a = decl("int_a", ArtifactRole::Intermediate);
    let int_b = decl("int_b", ArtifactRole::Intermediate);
    let out_a = decl("out_a", ArtifactRole::Target);
    let out_b = decl("out_b", ArtifactRole::Target);
    let stage_a = stage_registration(
        "stage_a",
        vec![in_a.artifact_id().clone()],
        vec![int_a.artifact_id().clone(), int_b.artifact_id().clone()],
        {
            let in_a = in_a.artifact_id().clone();
            move |_| vec![requirement(in_a.clone())]
        },
    );
    let stage_b = stage_registration(
        "stage_b",
        vec![int_a.artifact_id().clone()],
        vec![out_a.artifact_id().clone()],
        {
            let int_a = int_a.artifact_id().clone();
            move |_| vec![requirement(int_a.clone())]
        },
    );
    let stage_c = stage_registration(
        "stage_c",
        vec![int_b.artifact_id().clone()],
        vec![out_b.artifact_id().clone()],
        {
            let int_b = int_b.artifact_id().clone();
            move |_| vec![requirement(int_b.clone())]
        },
    );
    let manifest = manifest(
        vec![
            in_a.clone(),
            int_a.clone(),
            int_b.clone(),
            out_a.clone(),
            out_b.clone(),
        ],
        vec![stage_a, stage_b, stage_c],
        BTreeMap::new(),
    );
    let mut supplied = BTreeMap::new();
    supplied.insert(
        in_a.artifact_id().clone(),
        source_instance(&in_a, json!({"semantic_id": "in-a"})),
    );
    supplied.insert(
        int_a.artifact_id().clone(),
        source_instance(&int_a, json!({"semantic_id": "supplied-int-a"})),
    );

    let plan = evaluate(
        &manifest,
        supplied,
        set([out_a.artifact_id().clone(), out_b.artifact_id().clone()]),
        BTreeSet::new(),
        None,
    )
    .await?;

    assert_eq!(vec!["stage_a", "stage_b", "stage_c"], stage_ids(&plan));
    let stage_b = invocation(&plan, "stage_b");
    assert!(stage_b.input_bindings[0]
        .producer_invocation_key
        .is_none());
    let stage_c = invocation(&plan, "stage_c");
    assert!(stage_c.input_bindings[0]
        .producer_invocation_key
        .is_some());
    Ok(())
}

#[tokio::test]
async fn missing_required_input_raises_stage_planning_error() -> Result<()> {
    let in_a = decl("in_a", ArtifactRole::Source);
    let out_a = decl("out_a", ArtifactRole::Target);
    let stage = stage_registration(
        "stage_a",
        vec![in_a.artifact_id().clone()],
        vec![out_a.artifact_id().clone()],
        {
            let in_a = in_a.artifact_id().clone();
            move |_| vec![requirement(in_a.clone())]
        },
    );
    let manifest = manifest(vec![in_a, out_a.clone()], vec![stage], BTreeMap::new());

    let error = match evaluate(
        &manifest,
        BTreeMap::new(),
        set([out_a.artifact_id().clone()]),
        BTreeSet::new(),
        None,
    )
    .await
    {
        Ok(_) => panic!("missing required input should fail planning"),
        Err(error) => error,
    };

    assert!(error.to_string().contains("graph//in_a") || error.to_string().contains("producer"));
    Ok(())
}

#[tokio::test]
async fn ambiguous_producers_raise_stage_planning_error() -> Result<()> {
    let int_a = decl("int_a", ArtifactRole::Intermediate);
    let out_a = decl("out_a", ArtifactRole::Target);
    let producer_a = stage_registration(
        "producer_a",
        vec![],
        vec![int_a.artifact_id().clone()],
        |_| vec![],
    );
    let producer_b = stage_registration(
        "producer_b",
        vec![],
        vec![int_a.artifact_id().clone()],
        |_| vec![],
    );
    let consumer = stage_registration(
        "consumer",
        vec![int_a.artifact_id().clone()],
        vec![out_a.artifact_id().clone()],
        {
            let int_a = int_a.artifact_id().clone();
            move |_| vec![requirement(int_a.clone())]
        },
    );
    let manifest = manifest(
        vec![int_a, out_a.clone()],
        vec![producer_a, producer_b, consumer],
        BTreeMap::new(),
    );

    let error = match evaluate(
        &manifest,
        BTreeMap::new(),
        set([out_a.artifact_id().clone()]),
        BTreeSet::new(),
        None,
    )
    .await
    {
        Ok(_) => panic!("ambiguous producers should fail planning"),
        Err(error) => error,
    };

    assert!(error.to_string().contains("ambiguous") || error.to_string().contains("multiple"));
    Ok(())
}

#[tokio::test]
async fn default_producer_resolves_ambiguous_producer_choice() -> Result<()> {
    let int_a = decl("int_a", ArtifactRole::Intermediate);
    let out_a = decl("out_a", ArtifactRole::Target);
    let producer_a = stage_registration(
        "producer_a",
        vec![],
        vec![int_a.artifact_id().clone()],
        |_| vec![],
    );
    let producer_b = stage_registration(
        "producer_b",
        vec![],
        vec![int_a.artifact_id().clone()],
        |_| vec![],
    );
    let consumer = stage_registration(
        "consumer",
        vec![int_a.artifact_id().clone()],
        vec![out_a.artifact_id().clone()],
        {
            let int_a = int_a.artifact_id().clone();
            move |_| vec![requirement(int_a.clone())]
        },
    );
    let mut defaults = BTreeMap::new();
    defaults.insert(int_a.artifact_id().clone(), "producer_b".to_owned());
    let manifest = manifest(
        vec![int_a, out_a.clone()],
        vec![producer_a, producer_b, consumer],
        defaults,
    );

    let plan = evaluate(
        &manifest,
        BTreeMap::new(),
        set([out_a.artifact_id().clone()]),
        BTreeSet::new(),
        None,
    )
    .await?;

    assert_eq!(vec!["producer_b", "consumer"], stage_ids(&plan));
    Ok(())
}

#[tokio::test]
async fn phase_less_intermediate_request_is_terminal_remapped_to_family_id() -> Result<()> {
    let token_1 = decl(
        ArtifactKey::parse("transcript//token_text#1")?,
        ArtifactRole::Intermediate,
    );
    let token_2 = decl(
        ArtifactKey::parse("transcript//token_text#2")?,
        ArtifactRole::Intermediate,
    );
    let stage_1 = stage_registration(
        "stage_1",
        vec![],
        vec![token_1.artifact_id().clone()],
        |_| vec![],
    );
    let stage_2 = stage_registration(
        "stage_2",
        vec![token_1.artifact_id().clone()],
        vec![token_2.artifact_id().clone()],
        {
            let token_1 = token_1.artifact_id().clone();
            move |_| vec![requirement(token_1.clone())]
        },
    );
    let manifest = manifest(
        vec![token_1, token_2.clone()],
        vec![stage_1, stage_2],
        BTreeMap::new(),
    );
    let family = ArtifactKey::parse("transcript//token_text")?;

    let plan = evaluate(
        &manifest,
        BTreeMap::new(),
        set([family.clone()]),
        BTreeSet::new(),
        None,
    )
    .await?;

    assert_eq!(vec!["stage_1", "stage_2"], stage_ids(&plan));
    let binding = &invocation(&plan, "stage_2").requested_output_bindings[0];
    assert_eq!(token_2.artifact_id(), &binding.declared_artifact_id);
    assert_eq!(&family, &binding.resolved_artifact_id);
    assert!(binding.terminal_remapped());
    Ok(())
}

#[tokio::test]
async fn bounded_phase_requirement_selects_highest_producer_not_exceeding_bound() -> Result<()> {
    let token_1 = decl(
        ArtifactKey::parse("transcript//token_text#1")?,
        ArtifactRole::Intermediate,
    );
    let token_2 = decl(
        ArtifactKey::parse("transcript//token_text#2")?,
        ArtifactRole::Intermediate,
    );
    let token_3 = decl(
        ArtifactKey::parse("transcript//token_text#3")?,
        ArtifactRole::Intermediate,
    );
    let out_a = decl("out_a", ArtifactRole::Target);
    let producer_1 = stage_registration(
        "producer_1",
        vec![],
        vec![token_1.artifact_id().clone()],
        |_| vec![],
    );
    let producer_2 = stage_registration(
        "producer_2",
        vec![],
        vec![token_2.artifact_id().clone()],
        |_| vec![],
    );
    let producer_3 = stage_registration(
        "producer_3",
        vec![],
        vec![token_3.artifact_id().clone()],
        |_| vec![],
    );
    let consumer = stage_registration(
        "consumer",
        vec![ArtifactKey::parse("transcript//token_text#2")?],
        vec![out_a.artifact_id().clone()],
        |_| {
            vec![requirement(
                ArtifactKey::parse("transcript//token_text#2").unwrap(),
            )]
        },
    );
    let manifest = manifest(
        vec![token_1, token_2.clone(), token_3, out_a.clone()],
        vec![producer_1, producer_2, producer_3, consumer],
        BTreeMap::new(),
    );

    let plan = evaluate(
        &manifest,
        BTreeMap::new(),
        set([out_a.artifact_id().clone()]),
        BTreeSet::new(),
        None,
    )
    .await?;

    assert!(stage_ids(&plan).contains(&"producer_2"));
    assert!(!stage_ids(&plan).contains(&"producer_3"));
    let consumer = invocation(&plan, "consumer");
    assert_eq!(
        token_2.artifact_id(),
        &consumer.input_bindings[0].resolved_artifact_id
    );
    Ok(())
}

#[tokio::test]
async fn shared_stage_output_subset_expands_and_requirements_are_re_resolved() -> Result<()> {
    let in_a = decl("in_a", ArtifactRole::Source);
    let in_b = decl("in_b", ArtifactRole::Source);
    let int_a = decl("int_a", ArtifactRole::Intermediate);
    let int_b = decl("int_b", ArtifactRole::Intermediate);
    let out_a = decl("out_a", ArtifactRole::Target);
    let out_b = decl("out_b", ArtifactRole::Target);

    let shared = stage_registration_with_resolvers(
        "shared",
        vec![in_a.artifact_id().clone(), in_b.artifact_id().clone()],
        vec![int_a.artifact_id().clone(), int_b.artifact_id().clone()],
        {
            let in_a = in_a.artifact_id().clone();
            let in_b = in_b.artifact_id().clone();
            let int_b_id = int_b.artifact_id().clone();
            move |context| {
                let mut requirements = vec![requirement(in_a.clone())];
                if context.requested_outputs.contains(&int_b_id) {
                    requirements.push(requirement(in_b.clone()));
                }
                requirements
            }
        },
        None,
        None,
    );
    let consumer_a = stage_registration(
        "consumer_a",
        vec![int_a.artifact_id().clone()],
        vec![out_a.artifact_id().clone()],
        {
            let int_a = int_a.artifact_id().clone();
            move |_| vec![requirement(int_a.clone())]
        },
    );
    let consumer_b = stage_registration(
        "consumer_b",
        vec![int_b.artifact_id().clone()],
        vec![out_b.artifact_id().clone()],
        {
            let int_b = int_b.artifact_id().clone();
            move |_| vec![requirement(int_b.clone())]
        },
    );
    let manifest = manifest(
        vec![
            in_a.clone(),
            in_b.clone(),
            int_a.clone(),
            int_b.clone(),
            out_a.clone(),
            out_b.clone(),
        ],
        vec![shared, consumer_a, consumer_b],
        BTreeMap::new(),
    );
    let mut supplied = BTreeMap::new();
    supplied.insert(
        in_a.artifact_id().clone(),
        source_instance(&in_a, json!({"semantic_id": "in-a"})),
    );
    supplied.insert(
        in_b.artifact_id().clone(),
        source_instance(&in_b, json!({"semantic_id": "in-b"})),
    );

    let plan = evaluate(
        &manifest,
        supplied,
        set([out_a.artifact_id().clone(), out_b.artifact_id().clone()]),
        BTreeSet::new(),
        None,
    )
    .await?;

    let shared = invocation(&plan, "shared");
    assert_eq!(
        set([int_a.artifact_id().clone(), int_b.artifact_id().clone()]),
        shared.requested_output_ids().into_iter().collect()
    );
    assert_eq!(
        set([in_a.artifact_id().clone(), in_b.artifact_id().clone()]),
        shared
            .input_bindings
            .iter()
            .filter(|binding| binding.required)
            .map(|binding| binding.resolved_artifact_id.clone())
            .collect()
    );
    Ok(())
}

#[tokio::test]
async fn inherited_setting_wins_over_default_base_setting() -> Result<()> {
    let setting_a = decl(
        ArtifactKey::parse("settings//setting_a")?,
        ArtifactRole::Settings,
    );
    let setting_a_override = decl(
        ArtifactKey::parse("stage_prepare//settings//setting_a")?,
        ArtifactRole::Settings,
    );
    let in_a = decl("in_a", ArtifactRole::Source);
    let prepared = decl("prepared", ArtifactRole::Intermediate);
    let out_a = decl("out_a", ArtifactRole::Target);

    let prepare = stage_registration(
        "prepare",
        vec![in_a.artifact_id().clone(), setting_a.artifact_id().clone()],
        vec![prepared.artifact_id().clone()],
        {
            let in_a = in_a.artifact_id().clone();
            let setting_a = setting_a.artifact_id().clone();
            move |_| vec![requirement(in_a.clone()), requirement(setting_a.clone())]
        },
    );
    let render = stage_registration(
        "render",
        vec![
            prepared.artifact_id().clone(),
            setting_a_override.artifact_id().clone(),
        ],
        vec![out_a.artifact_id().clone()],
        {
            let prepared = prepared.artifact_id().clone();
            let setting_a_override = setting_a_override.artifact_id().clone();
            move |_| {
                vec![ArtifactRequirement {
                    artifact_id: prepared.clone(),
                    required: true,
                    phase_match: PhaseMatchMode::AtMost,
                    pushed_settings: vec![setting_a_override.clone()],
                }]
            }
        },
    );
    let manifest = manifest(
        vec![
            setting_a.clone(),
            setting_a_override.clone(),
            in_a.clone(),
            prepared.clone(),
            out_a.clone(),
        ],
        vec![prepare, render],
        BTreeMap::new(),
    );
    let mut supplied = BTreeMap::new();
    supplied.insert(
        in_a.artifact_id().clone(),
        source_instance(&in_a, json!({"semantic_id": "in-a"})),
    );
    supplied.insert(
        setting_a.artifact_id().clone(),
        source_instance(&setting_a, json!({"sample_rate_hz": 22050})),
    );
    supplied.insert(
        setting_a_override.artifact_id().clone(),
        source_instance(&setting_a_override, json!({"sample_rate_hz": 16000})),
    );

    let plan = evaluate(
        &manifest,
        supplied,
        set([out_a.artifact_id().clone()]),
        set([setting_a_override.artifact_id().clone()]),
        None,
    )
    .await?;

    let prepare = invocation(&plan, "prepare");
    assert_eq!(
        Some(&json!({"sample_rate_hz": 16000})),
        prepare.resolved_settings.get(setting_a.artifact_id())
    );
    Ok(())
}

#[test]
fn manifest_validation_rejects_static_candidate_graph_cycle() -> Result<()> {
    let int_a = decl("int_a", ArtifactRole::Intermediate);
    let int_b = decl("int_b", ArtifactRole::Intermediate);
    let stage_a = stage_registration(
        "stage_a",
        vec![int_b.artifact_id().clone()],
        vec![int_a.artifact_id().clone()],
        {
            let int_b = int_b.artifact_id().clone();
            move |_| vec![requirement(int_b.clone())]
        },
    );
    let stage_b = stage_registration(
        "stage_b",
        vec![int_a.artifact_id().clone()],
        vec![int_b.artifact_id().clone()],
        {
            let int_a = int_a.artifact_id().clone();
            move |_| vec![requirement(int_a.clone())]
        },
    );

    let mut builder = StageManifestBuilder::new();
    builder.add_artifact(int_a.erase())?;
    builder.add_artifact(int_b.erase())?;
    builder.add_stage(stage_a)?;
    builder.add_stage(stage_b)?;

    let error = match builder.freeze() {
        Ok(_) => panic!("cycle should be rejected"),
        Err(error) => error,
    };
    assert!(error.to_string().contains("cycle"));
    Ok(())
}

fn stage_ids<'a>(
    plan: &'a chunky_example::task_request_graph::TaskRequestGraph<'a>,
) -> Vec<&'a str> {
    plan.invocations
        .iter()
        .map(|invocation| invocation.registration.stage_id())
        .collect()
}

fn invocation<'a>(
    plan: &'a chunky_example::task_request_graph::TaskRequestGraph<'a>,
    stage_id: &str,
) -> &'a chunky_example::task_request_graph::PlannedInvocation<'a> {
    plan.invocations
        .iter()
        .find(|invocation| invocation.registration.stage_id() == stage_id)
        .unwrap_or_else(|| panic!("missing invocation {stage_id}"))
}

use coltec_daemon::{workspace_schema, WorkspaceSpec};
use pretty_assertions::assert_eq;
use schemars::gen::SchemaSettings;
use serde_json::Value;
use std::fs;
use std::path::PathBuf;

#[test]
fn parses_valid_fixture() {
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let data = fs::read_to_string(
        manifest_dir.join("tests/data/valid_workspace.yaml"),
    )
    .expect("fixture present");

    let spec: WorkspaceSpec =
        serde_yaml::from_str(&data).expect("yaml parses into spec");

    assert_eq!(spec.name, "env-test");
    assert_eq!(spec.metadata.org, "test-org");
    assert!(spec.persistence.enabled);
    assert_eq!(spec.devcontainer.user, "vscode");
}

#[test]
fn generates_json_schema() {
    let schema = workspace_schema();
    let json = serde_json::to_value(&schema).expect("schema serializable");

    // Basic smoke checks on generated schema content
    assert!(json.get("properties").is_some());
    assert!(json.pointer("/properties/name").is_some());
    let has_def = json
        .pointer("/$defs/DevcontainerSpec")
        .or_else(|| json.pointer("/definitions/DevcontainerSpec"));
    assert!(has_def.is_some());
}

#[test]
fn schema_generation_is_stable() {
    // Ensure using default settings does not panic; this is a guard for future refactors.
    let settings = SchemaSettings::draft2019_09();
    let generator = settings.into_generator();
    let schema = generator.into_root_schema_for::<WorkspaceSpec>();
    let json = serde_json::to_value(&schema).expect("schema serializable");

    assert!(matches!(json.get("type"), Some(Value::String(s)) if s == "object"));
}

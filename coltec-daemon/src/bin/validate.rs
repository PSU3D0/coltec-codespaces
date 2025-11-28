use clap::Parser;
use coltec_daemon::{workspace_schema, WorkspaceSpec};
use jsonschema::{Draft, JSONSchema};
use serde_json::Value;
use std::fs::File;
use std::io::Read;
use std::path::PathBuf;

/// Validate a workspace-spec YAML against the generated JSON Schema and semantic invariants.
#[derive(Parser, Debug)]
#[command(version, about)]
struct Args {
    /// Path to workspace-spec.yaml (required unless --generate-schema)
    #[arg(short, long, required_unless_present = "generate_schema")]
    file: Option<PathBuf>,

    /// Optional path to a JSON Schema file; if omitted, uses the embedded schema from the Rust types.
    #[arg(short, long)]
    schema: Option<PathBuf>,

    /// Skip semantic validation (only run JSON Schema validation)
    #[arg(long)]
    schema_only: bool,

    /// Output the embedded JSON Schema and exit
    #[arg(long)]
    generate_schema: bool,
}

fn load_json_schema(schema_path: &PathBuf) -> anyhow::Result<Value> {
    let mut f = File::open(schema_path)?;
    let mut buf = String::new();
    f.read_to_string(&mut buf)?;
    let json: Value = serde_json::from_str(&buf)?;
    Ok(json)
}

fn main() -> anyhow::Result<()> {
    let args = Args::parse();

    // Handle --generate-schema
    if args.generate_schema {
        let schema = workspace_schema();
        let json = serde_json::to_string_pretty(&schema)?;
        println!("{}", json);
        return Ok(());
    }

    let file = args.file.expect("file is required unless --generate-schema");

    let schema_json = if let Some(path) = args.schema {
        load_json_schema(&path)?
    } else {
        serde_json::to_value(workspace_schema())?
    };

    // jsonschema crate needs the schema to live for 'static; leaking here is fine for a short-lived CLI.
    let schema_ref: &'static Value = Box::leak(Box::new(schema_json));

    // jsonschema crate needs serde_json::Value for both schema and instance.
    let compiled = JSONSchema::options()
        .with_draft(Draft::Draft7)
        .compile(schema_ref)?;

    let file_content = std::fs::read_to_string(&file)?;
    let data: Value = serde_yaml::from_str(&file_content)?;

    // Step 1: JSON Schema validation
    let result = compiled.validate(&data);

    if let Err(errors) = result {
        eprintln!("✗ schema invalid: {}", file.display());
        let messages: Vec<String> = errors
            .map(|err| {
                let instance_path = err.instance_path.to_string();
                let schema_path = err.schema_path.to_string();
                format!("  - at {} (schema {}): {}", instance_path, schema_path, err)
            })
            .collect();

        for msg in &messages {
            eprintln!("{msg}");
        }

        anyhow::bail!("schema validation failed");
    }

    // Step 2: Semantic validation (unless --schema-only)
    if !args.schema_only {
        let spec: WorkspaceSpec = serde_yaml::from_str(&file_content)?;

        if let Err(err) = spec.validate_semantics() {
            eprintln!("✗ semantic error: {}", file.display());
            eprintln!("  - {}", err);
            anyhow::bail!("semantic validation failed");
        }
    }

    println!("✓ valid: {}", file.display());
    Ok(())
}

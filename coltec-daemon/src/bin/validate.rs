use clap::Parser;
use coltec_daemon::workspace_schema;
use jsonschema::{Draft, JSONSchema};
use serde_json::Value;
use std::fs::File;
use std::io::Read;
use std::path::PathBuf;

/// Validate a workspace-spec YAML against the generated JSON Schema.
#[derive(Parser, Debug)]
#[command(version, about)]
struct Args {
    /// Path to workspace-spec.yaml
    #[arg(short, long)]
    file: PathBuf,

    /// Optional path to a JSON Schema file; if omitted, uses the embedded schema from the Rust types.
    #[arg(short, long)]
    schema: Option<PathBuf>,
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

    let data: Value = serde_yaml::from_reader(File::open(&args.file)?)?;

    let result = compiled.validate(&data);

    if let Err(errors) = result {
        eprintln!("✗ invalid: {}", args.file.display());
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

        anyhow::bail!("validation failed");
    }

    println!("✓ valid: {}", args.file.display());
    Ok(())
}

//! coltec-daemon: Sync daemon for Coltec devcontainer workspaces.
//!
//! This daemon reads workspace-spec.yaml and orchestrates file synchronization
//! via rclone. It supports:
//! - Bidirectional sync (bisync)
//! - Push-only and pull-only modes
//! - Configurable intervals per sync path
//! - Graceful shutdown on SIGINT/SIGTERM

use anyhow::Result;
use clap::Parser;
use tokio::sync::watch;
use tokio::time::{interval, Duration};
use tracing::{debug, error, info, warn};

mod cli;

use cli::{Args, LogFormat};
use coltec_daemon::{build_plan, execute_plan, load_and_validate, SyncPlan};

/// Exit codes following Unix conventions.
mod exit_code {
    /// Success
    pub const SUCCESS: i32 = 0;
    /// Configuration error (invalid YAML, schema, or semantics)
    pub const CONFIG_ERROR: i32 = 1;
    /// Runtime error (rclone failure, network error)
    pub const RUNTIME_ERROR: i32 = 2;
    /// Terminated by SIGINT (128 + 2)
    pub const SIGINT: i32 = 130;
    /// Terminated by SIGTERM (128 + 15)
    pub const SIGTERM: i32 = 143;
}

/// Which signal caused shutdown.
#[derive(Debug, Clone, Copy)]
enum ShutdownSignal {
    SigInt,
    SigTerm,
}

#[tokio::main]
async fn main() {
    let args = Args::parse();

    // Initialize tracing before anything else
    init_tracing(&args);

    match run(args).await {
        Ok(None) => {
            // Normal exit (--once, --validate-only, or persistence disabled)
            std::process::exit(exit_code::SUCCESS);
        }
        Ok(Some(ShutdownSignal::SigInt)) => {
            std::process::exit(exit_code::SIGINT);
        }
        Ok(Some(ShutdownSignal::SigTerm)) => {
            std::process::exit(exit_code::SIGTERM);
        }
        Err(e) => {
            // Print error nicely for config errors
            print_error(&e);
            std::process::exit(exit_code::CONFIG_ERROR);
        }
    }
}

/// Print an error with context, using colors if stderr is a tty.
fn print_error(err: &anyhow::Error) {
    let is_tty = std::io::IsTerminal::is_terminal(&std::io::stderr());

    if is_tty {
        // Red bold for "error:"
        eprintln!("\x1b[1;31merror:\x1b[0m {}", err);
        for cause in err.chain().skip(1) {
            eprintln!("  \x1b[1;33mcaused by:\x1b[0m {}", cause);
        }
    } else {
        eprintln!("error: {}", err);
        for cause in err.chain().skip(1) {
            eprintln!("  caused by: {}", cause);
        }
    }

    // Also log via tracing for JSON output
    error!(error = %err, "daemon failed");
}

/// Run the daemon. Returns None for normal exit, Some(signal) for signal-induced exit.
async fn run(args: Args) -> Result<Option<ShutdownSignal>> {
    info!(
        config = %args.config.display(),
        dry_run = args.dry_run,
        once = args.once,
        "coltec-daemon starting"
    );

    // Load and validate config (JSON Schema + semantic validation)
    let spec = load_and_validate(&args.config)?;

    info!(
        workspace = %spec.name,
        org = %spec.metadata.org,
        project = %spec.metadata.project,
        "configuration loaded"
    );

    // Handle --validate-only
    if args.validate_only {
        let is_tty = std::io::IsTerminal::is_terminal(&std::io::stdout());
        if is_tty {
            println!(
                "\x1b[1;32m✓\x1b[0m configuration valid: {}",
                args.config.display()
            );
        } else {
            println!("configuration valid: {}", args.config.display());
        }
        return Ok(None);
    }

    // Check persistence enabled
    if !spec.persistence.enabled {
        info!("persistence disabled, nothing to sync");
        return Ok(None);
    }

    // Build sync plan
    let plan = build_plan(&spec, args.interval);

    // Apply path filter if specified
    let plan = if args.only_paths.is_empty() {
        plan
    } else {
        let filtered = plan.filter_by_names(&args.only_paths);
        info!(
            filter = ?args.only_paths,
            before = plan.actions.len(),
            after = filtered.actions.len(),
            "filtered sync paths"
        );
        filtered
    };

    if plan.is_empty() {
        warn!("no sync actions in plan");
        return Ok(None);
    }

    info!(
        actions = plan.actions.len(),
        min_interval = plan.min_interval(),
        "sync plan built"
    );

    // Log planned actions in debug mode
    for action in &plan.actions {
        debug!(
            name = %action.name,
            local = %action.local_path,
            remote = %action.remote_path,
            direction = ?action.direction,
            interval = action.interval_secs,
            priority = action.priority,
            remote_type = ?action.remote.as_ref().map(|r| &r.remote_type),
            "planned sync action"
        );
    }

    // Execute sync
    if args.once {
        // Single pass mode
        info!("running single sync pass");
        let result = execute_plan(&plan, args.dry_run).await?;

        if result.all_success() {
            info!(success = result.success_count, "single sync pass complete");
        } else {
            error!(
                success = result.success_count,
                failed = result.failure_count,
                "sync pass completed with errors"
            );
            for r in &result.results {
                if !r.success {
                    error!(
                        name = %r.name,
                        error = ?r.error,
                        "sync failed"
                    );
                }
            }
            std::process::exit(exit_code::RUNTIME_ERROR);
        }
        Ok(None)
    } else {
        // Continuous mode with signal handling
        let signal = run_continuous(plan, args.dry_run).await?;
        Ok(Some(signal))
    }
}

/// Run the continuous sync loop with graceful shutdown on signals.
/// Returns which signal caused the shutdown.
async fn run_continuous(plan: SyncPlan, dry_run: bool) -> Result<ShutdownSignal> {
    let interval_secs = plan.min_interval();

    info!(
        interval_secs = interval_secs,
        "starting continuous sync loop"
    );

    // Create shutdown channel
    let (shutdown_tx, shutdown_rx) = watch::channel(false);

    // Spawn the sync loop
    let sync_handle = tokio::spawn(sync_loop(plan, dry_run, interval_secs, shutdown_rx));

    // Wait for shutdown signal
    let signal = wait_for_shutdown_signal().await;

    // Signal shutdown to sync loop
    info!("signaling shutdown to sync loop...");
    let _ = shutdown_tx.send(true);

    // Wait for sync loop to finish (with timeout)
    match tokio::time::timeout(Duration::from_secs(30), sync_handle).await {
        Ok(Ok(())) => {
            info!("sync loop stopped cleanly");
        }
        Ok(Err(e)) => {
            error!(error = %e, "sync loop panicked");
        }
        Err(_) => {
            warn!("sync loop did not stop within 30s timeout");
        }
    }

    info!("shutdown complete");
    Ok(signal)
}

/// The main sync loop - runs until shutdown signal received.
async fn sync_loop(
    plan: SyncPlan,
    dry_run: bool,
    interval_secs: u64,
    mut shutdown_rx: watch::Receiver<bool>,
) {
    let mut ticker = interval(Duration::from_secs(interval_secs));

    // Run first sync immediately
    info!("running initial sync pass");
    run_one_pass(&plan, dry_run).await;

    loop {
        tokio::select! {
            // Wait for next tick
            _ = ticker.tick() => {
                // Check if shutdown requested before starting sync
                if *shutdown_rx.borrow() {
                    info!("shutdown requested, exiting loop");
                    break;
                }

                debug!("interval elapsed, starting sync pass");
                run_one_pass(&plan, dry_run).await;
            }

            // Watch for shutdown signal
            _ = shutdown_rx.changed() => {
                if *shutdown_rx.borrow() {
                    info!("shutdown signal received, exiting loop");
                    break;
                }
            }
        }
    }
}

/// Run a single sync pass, logging results.
async fn run_one_pass(plan: &SyncPlan, dry_run: bool) {
    match execute_plan(plan, dry_run).await {
        Ok(result) => {
            if result.all_success() {
                info!(success = result.success_count, "sync pass complete");
            } else {
                warn!(
                    success = result.success_count,
                    failed = result.failure_count,
                    "sync pass completed with errors"
                );
                for r in &result.results {
                    if !r.success {
                        warn!(
                            name = %r.name,
                            error = ?r.error,
                            "sync failed"
                        );
                    }
                }
            }
        }
        Err(e) => {
            error!(error = %e, "sync pass failed");
        }
    }
}

/// Wait for SIGINT (Ctrl+C) or SIGTERM. Returns which signal was received.
async fn wait_for_shutdown_signal() -> ShutdownSignal {
    #[cfg(unix)]
    {
        use tokio::signal::unix::{signal, SignalKind};

        let mut sigterm =
            signal(SignalKind::terminate()).expect("failed to install SIGTERM handler");

        tokio::select! {
            result = tokio::signal::ctrl_c() => {
                match result {
                    Ok(()) => info!("received SIGINT (Ctrl+C)"),
                    Err(e) => error!(error = %e, "failed to listen for Ctrl+C"),
                }
                ShutdownSignal::SigInt
            }
            _ = sigterm.recv() => {
                info!("received SIGTERM");
                ShutdownSignal::SigTerm
            }
        }
    }

    #[cfg(not(unix))]
    {
        // On non-Unix platforms, just handle Ctrl+C
        match tokio::signal::ctrl_c().await {
            Ok(()) => info!("received Ctrl+C"),
            Err(e) => error!(error = %e, "failed to listen for Ctrl+C"),
        }
        ShutdownSignal::SigInt
    }
}

/// Initialize the tracing subscriber based on CLI args.
fn init_tracing(args: &Args) {
    use tracing_subscriber::{fmt, prelude::*, EnvFilter};

    // Build filter from RUST_LOG env or --log-level arg
    let filter = EnvFilter::try_from_default_env().unwrap_or_else(|_| {
        // Use the log_level arg, prefixed with crate name for specificity
        EnvFilter::new(format!("coltec_daemon={}", args.log_level))
    });

    match args.log_format {
        LogFormat::Json => {
            tracing_subscriber::registry()
                .with(filter)
                .with(fmt::layer().json())
                .init();
        }
        LogFormat::Text => {
            tracing_subscriber::registry()
                .with(filter)
                .with(fmt::layer())
                .init();
        }
    }
}

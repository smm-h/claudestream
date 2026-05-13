# claudewheel backend integration

## Problem
claudewheel's print mode currently `os.execvpe`s the claude binary directly. It should optionally use claudestream as a backend for stream parsing and output rendering.

## Solution
In claudewheel's print mode path, instead of `os.execvpe(claude, [..., "--print", prompt])`, call `claudestream.print_prompt(prompt, model=..., cwd=..., env=...)`. claudewheel owns config resolution (profile, model, directory, env vars). claudestream owns subprocess management and stream parsing.

## Affected repos
- claudewheel (Python, ~/Projects/claudewheel)
- claudestream (this project — may need API adjustments based on claudewheel's needs)

## Effort
Small — claudestream.print_prompt already exists. Main work is in claudewheel.

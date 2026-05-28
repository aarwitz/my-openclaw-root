# Git Workflow for Phase II Research Pipeline

## Quick Start

```bash
# Check current branch
git branch

# Pull latest changes
git pull

# Check what changed
git diff HEAD~1

# Make a change to a file
nano phase2/scoring.py

# Stage your changes
git add phase2/scoring.py

# Commit with message
git commit -am "Adjust extension penalty window from 1.5-2.5 ATR to 1.2-2.4 ATR"

# Push to remote (when set up)
git push origin master
```

## Common Workflow

### 1. Create a Feature Branch
```bash
git checkout -b feature/calibrate-vol-efficiency
# Make changes, test, commit
git add phase2/scoring.py
git commit -m "Adjust vol-efficiency formula constant from 0.03 to 0.025"
```

### 2. View Changes Before Committing
```bash
# See what files changed
git status

# See diff for a file
git diff phase2/regime.py

# See staged changes
git diff --cached
```

### 3. Review Commit History
```bash
# Last 5 commits with one-liner
git log --oneline -5

# Full commit with diff
git log -p -1

# Find who changed what
git blame phase2/scoring.py | grep "extension_penalty"
```

## Key Files & Branches

**Core Files (READ CAREFULLY BEFORE EDITING):**
- `phase2/scoring.py` — All calibration constants in lines 25-60
- `phase2/schema.py` — CandidateRecord dataclass + enums
- `phase2/regime.py` — SPY/VIX classifier thresholds
- `phase2/setup_classifier.py` — 6-state setup precedence rules

**Documentation:**
- `AUTONOMOUS_PM_OPERATING_MODEL.md` — Source authority + scoring framework
- `phase2/README.md` — Design principles + cache explanation
- `phase2/RULES.md` — Full calibration reference

## Testing Before Committing

Always run tests before pushing:
```bash
python3 -m phase2.tests
```

All 13 tests must pass. If a test fails, see `tests.py` for what it validates:
- `test_regime_*` (5 tests): SPY/VIX classification
- `test_setup_*` (3 tests): Setup state rules  
- `test_scoring_*` (5 tests): Score bucket logic + penalties

## Validation Checklist

Before committing calibration changes:
- [ ] Run `python3 -m phase2.tests` — all pass
- [ ] Test on NVDA: `python3 -m phase2.cli normalize --ticker NVDA`
- [ ] Verify setup_state is meaningful (not "none")
- [ ] Check score is in valid range (0-100)
- [ ] Review git diff before commit: `git diff --cached`
- [ ] Write commit message describing WHAT changed and WHY

## Undoing Changes

```bash
# Discard all unsaved changes to a file
git checkout phase2/scoring.py

# Undo last commit (keeps changes staged)
git reset --soft HEAD~1

# View what was undone
git show HEAD

# Revert a bad commit (creates new commit that undoes it)
git revert <commit-hash>
```

## Using Git Skill in OpenClaw

The `git` skill is available via:
```
druck-research skill can use git
```

This allows you to ask Git questions like:
- "Show me what changed in the last commit"
- "Find when extension_penalty was modified"
- "Compare my changes to master"
- "What's the history of setup_classifier.py?"

## Questions?

Check the most recent README or ask for help understanding a specific module.

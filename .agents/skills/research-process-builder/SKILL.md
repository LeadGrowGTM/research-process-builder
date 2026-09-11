---
name: research-process-builder
description: Build validated web research processes through self-annealing loops. Takes a research goal, generates search steps, tests against sample companies, scores accuracy, and iterates until 90%+. Use when creating new research workflows, building claygent/agent prompts, or systematizing any web research task.
---

# Research Process Builder

Factory that produces validated, step-by-step web research processes through iterative testing. Takes any research goal, generates search patterns, tests against real companies, scores accuracy, and loops until 90%+ reliability.

Output: portable `.md` process files any agent (Clay, custom GPT, browser agent, coding agent) can follow.

## When To Use

- Building a new research workflow for any topic (company intel, market sizing, hiring signals, tech stack detection)
- Creating claygent or web research agent prompts that need to work reliably
- Systematizing any manual web research you do repeatedly

## When NOT To Use

- Running an existing research process (load the process `.md` directly)
- One-off research where you just need the answer
- Data enrichment at scale (use a dedicated enrichment tool)

## Interactive Flow

@references/interactive-flow.md

## Example Processes

| Process                   | File                                  | Steps | Accuracy |
| ------------------------- | ------------------------------------- | ----- | -------- |
| Find competitors          | `processes/find-competitors.md`       | 7     | 93%      |
| Find reviews              | `processes/find-reviews.md`           | 6     | 95%      |
| Find recent news          | `processes/find-news.md`              | 7     | 90%      |
| Find PR/releases          | `processes/find-pr-releases.md`       | 5     | 90%      |
| Find third-party profiles | `processes/find-profiles.md`          | 6     | 100%     |
| Find hiring activity      | `processes/find-hiring.md`            | 5     | 93%      |
| Find job role insights    | `processes/find-job-role-insights.md` | 5     | 90%      |
| Find growth signals       | `processes/find-growth-signals.md`    | 7     | 93%      |
| Find customer negativity  | `processes/find-negativity.md`        | 6     | 90%      |

## Build Methodology

@references/build-loop.md

## Accumulated Learnings

@references/learnings.md

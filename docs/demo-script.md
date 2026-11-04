# Demo script (3 minutes)

## The story in one line

**Meetings make promises. Notion records tasks. Nobody connects them. ProjectPulse does, and asks a person before it changes anything.**

The arc: *a promise is made in a meeting → ProjectPulse finds it → a human approves → later evidence arrives → the loop closes with proof.* Everything on screen should serve that arc. If a beat doesn't, cut it.

## Beats

The project is **University Circle Dashboard** in Notion. Four tasks: configure the NUC (Adam), deliver the display (John), drill the mounting points (Matthew), install the display (Alex, due Nov 14).

| Time | On screen | Say |
|---|---|---|
| 0:00 | Caption: **"Built during the Meta Global AI Developer Hackathon."** Then the Notion Tasks board for University Circle, all four tasks *Not-started*. | "I manage about 20 meetings a week, and I spend around 15 hours a week making Notion match what was said in them. We don't have a meeting-notes problem. We have a follow-through problem." |
| 0:20 | ProjectPulse, **Add source → Follow-up email**. Press the example button, Analyze. One card: **Verified: attachment**. | "John emails that he delivered the display, with the receipt attached. An attachment is independent evidence, so ProjectPulse marks it verified, not just claimed." |
| 0:40 | Approve, **Apply**. Cut to Notion: *Pick up and deliver the digital display* is **Done**, with the evidence note. | "Nothing is written until I press Apply. Now Notion shows it done, with the proof attached." |
| 0:55 | **Add source**, choose `meeting.wav`, Analyze. The progress list ticks: transcribing, finding commitments, matching. **Speed this part up.** | "The next day, a prep meeting. Muse Voice Transcribe gives one voice per speaker. Muse Spark finds the commitments and claims and matches them to the right project and task." |
| 1:20 | **Review Inbox.** Pause on the header chips: *Speaker A = John, B = Adam, C = Matthew*. | "The recording only says 'speaker A, B, C'. Spark works out who is who from how people address each other." |
| 1:35 | Adam's and Matthew's cards: **Possible completion, Claimed, not verified.** Show the box: *Status: Not-started → Done*. | "Adam says the NUC is configured. Matthew says the holes are drilled. Those are claims, not proof, so a person decides. It shows exactly what will change." |
| 1:50 | Point out what is missing: no card for John's 'the display has been delivered'. | "John repeated that the display was delivered. That is already Done, so there is nothing to do and no noise." |
| 2:00 | Approve both, **Apply**. Cut to Notion: three tasks **Done**; Alex's installation still *Not-started*. | "Three done, with evidence. And Alex's installation is still open, exactly as it should be. No duplicate task was created." |
| 2:20 | **Follow-Up Center.** Open the NUC task: the quote from the audio, the history. | "Every change traces back to the words that justified it." |
| 2:40 | Re-upload the same file: *"You have already added this."* | "It won't process the same source twice." |
| 2:50 | Title card with the repo link. | "The model interprets. The backend validates. A human decides. Built with Muse Voice Transcribe and Muse Spark, LangGraph and Postgres." |

If it runs long, cut in this order: the duplicate re-upload, then the Follow-Up Center beat. Never cut the claim-versus-verified contrast or the Notion before-and-after.

## Preparing the recording

1. **Use a separate database for the demo** so the reset script never touches real meetings:
   ```bash
   createdb projectpulse_demo
   cd backend
   export DATABASE_URL=postgresql:///projectpulse_demo
   uv run python -m app.db && uv run python -m app.notion_sync
   uv run python ../scripts/reset_demo.py --save     # while Notion is in its starting state
   uv run uvicorn app.api:app --port 8077
   ```
2. Between takes: `uv run python ../scripts/reset_demo.py --yes` (same `DATABASE_URL`). It restores Notion and clears the demo data.
3. **Set the dates to match the story**: the follow-up email on `2026-11-12`, the meeting on `2026-11-13`, whatever day you record.
4. Window about 1280x800, browser zoom 100%, light or dark mode as you prefer. Use the **Follow-up email** example button.
5. Put the Notion Tasks board in a second tab, ready to show before and after.
6. Run the whole thing once before recording. Transcription and the two Spark passes take about a minute, so record that step and speed it up.

## What the judges score, and where it shows

| Criterion | Where it shows |
|---|---|
| **Impact (30%)** | A real pain: commitments lost between meetings and the board. Say it's a problem you face at work. |
| **Use of Meta tooling (25%)** | Muse Voice Transcribe with diarization (three voices). Muse Spark for schema-constrained extraction and matching. The model is a judge, never an actor. |
| **Technical depth (20%)** | Human-in-the-loop pause, validation against live Notion before any write, idempotent retryable writes, project-scoped matching. One sentence is enough. |
| **Demo (25%)** | One continuous, working flow with real writes to Notion. No slides in the middle. |

## Claims to stay honest about

- Say "proposes", not "decides". Every change needs a person.
- Don't say it's always right. Model output varies, and that's why a human reviews.
- It takes WAV recordings and pasted text. Zoom and Gmail integrations are future work.
- The demo data is synthetic.

## If something goes wrong on camera

- **A run fails:** the progress list shows the step and a **Retry** button. Retrying is safe, because writes are idempotent.
- **A card looks different from this script:** model output varies a little. Describe what's on screen; the story still holds.
- **Anything odd after a take:** run the reset and record again.

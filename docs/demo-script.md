# Demo script (3 minutes)

## The story in one line

**Meetings make promises. Notion records tasks. Nobody connects them. ProjectPulse does, and asks a person before it changes anything.**

The arc: *a promise is made in a meeting → ProjectPulse finds it → a human approves → later evidence arrives → the loop closes with proof.* Everything on screen should serve that arc. If a beat doesn't, cut it.

## Beats

| Time | On screen | Say |
|---|---|---|
| 0:00 | Caption: **"Built during the Meta Global AI Developer Hackathon."** Then the Notion Tasks board: *Finalize cabling plan: Not-started*, *Send revised hardware specs: In-progress*. | "Every project team has this meeting. Someone says 'I'll send it by Friday.' Someone says 'I already sent the plan.' And then nothing connects those words to the board." |
| 0:15 | ProjectPulse, **Add source**. | "ProjectPulse is a follow-through agent. It connects what people say to the work you already track, and it never changes your board without a human saying yes." |
| 0:25 | Choose `meeting.wav`, press Analyze. The progress list ticks: transcribing, finding commitments, matching. **Speed this part up.** | "Here's a five-person meeting. Muse Voice Transcribe turns it into text with a separate voice for each speaker. Muse Spark finds the commitments, claims and updates, and matches them to the right project and task." |
| 0:50 | **Review Inbox.** Pause on the header chips: *Speaker A = Dana, B = Priya, C = Marcus, E = Alex*. | "The recording only says 'speaker A, B, C.' Spark works out who is who from how people address each other. Nobody addressed Sam, so it doesn't guess." |
| 1:05 | Sam's card: **Suggestion**. | "Someone said 'we could consider contacting the vendor.' That's a suggestion, not a commitment, so it does not become a task." |
| 1:15 | Priya's card: **Possible completion, Claimed, not verified.** Show the box: *Status: Not-started → Done*. | "Priya says she sent the plan. That's a claim, not proof. ProjectPulse can propose closing the task, but it can't close it. And it shows exactly what will change." |
| 1:30 | Dana's card: linked task. Mention the look-alike task in another project. | "Dana's promise links to the existing task. There's a near-identical task in a different project. It picked the right one, so no duplicate and no mix-up." |
| 1:40 | Approve each. The bar: *"All decided. Nothing is written to Notion until you apply."* Press **Apply**. Cut to Notion: cabling task **Done**, with the evidence note. | "Nothing is written until I press Apply. And now Notion shows the task done, with the quote that justified it." |
| 2:00 | **Add source → Follow-up email**. Press the example button, Analyze (about 20 s, trim it). One card: **Verified: attachment**. Approve, Apply. | "A day later, Dana emails the specs. This time there's an attachment, which is independent evidence, so it's marked verified. Not claimed." |
| 2:20 | **Follow-Up Center.** Open Dana's task. Scroll the panel: original quote from the audio, the verified email, the history. | "And here's the full circle. The promise from the recording, the proof from the email, and every step in between." |
| 2:40 | Marcus's task: **Blocked, overdue.** Then re-upload the same file: *"You have already added this."* | "It also shows what's slipping, like Marcus's blocker, and it won't process the same source twice." |
| 2:50 | Title card with the repo link. | "The model interprets. The backend validates. A human decides. Built with Muse Voice Transcribe and Muse Spark, LangGraph and Postgres." |

If it runs long, cut in this order: the duplicate re-upload, then Sam's card, then the Marcus beat. Never cut the Priya claim, the email, or the Follow-Up Center panel.

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
3. **Set the meeting date to `2026-11-03` (a Tuesday), time 09:00**, whatever day you record. "By Friday" then resolves to Nov 6 and "by Wednesday" to Nov 4, matching the Notion tasks, so there are no surprise conflicts. For the follow-up email, use the same date.
4. Window about 1280x800, browser zoom 100%, light or dark mode as you prefer. Use the **Follow-up email** example button.
5. Put the Notion Tasks board in a second tab, ready to show before and after.
6. Run the whole thing once before recording. Transcription and the two Spark passes take about a minute, so record that step and speed it up.

## What the judges score, and where it shows

| Criterion | Where it shows |
|---|---|
| **Impact (30%)** | A real pain: commitments lost between meetings and the board. Say it's a problem you face at work. |
| **Use of Meta tooling (25%)** | Muse Voice Transcribe with diarization (five voices). Muse Spark for schema-constrained extraction and matching. The model is a judge, never an actor. |
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

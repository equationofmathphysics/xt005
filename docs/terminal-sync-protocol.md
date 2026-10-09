# Terminal Synchronization Protocol

The PTY is the source of truth. Browsers are disposable renderers and must be
able to attach, fall behind, recover, and continue without asking the PTY to
replay an arbitrary ANSI tail.

## Server State

Each terminal owns:

- one PTY and reader thread;
- a bounded raw-text history with absolute Unicode code-point offsets;
- an authoritative terminal screen model with normal and alternate buffers;
- a stable `stream_id` and process-monotonic `stream_epoch`;
- a monotonically increasing `frame_seq` covering output and resize events;
- one delivery lock that orders state mutation, room delivery, attach frames,
  recovery frames, output, resize, and exit notifications.

Terminal close/replacement enters the write and delivery boundaries before the
instance is detached, so an old fd or stream cannot emit after replacement.
PTY input has a separate write lock; a blocked paste never prevents the reader
from draining output and updating the screen model.

An authoritative frame serializes the screen, scrollback, active SGR, cursor,
saved cursor, margins, modes, charset, tab stops, and both buffers when the
alternate buffer is active. `screen_snapshot_delta_safe: true` means raw PTY
output can continue from that frame.

## Attach And Recovery

`terminal_ready` carries one authoritative frame. While the frame is being
written, the browser buffers structured `terminal_output` events. When the
xterm write callback completes, it discards events included in the frame and
applies only the remaining contiguous events.

Every writable client event carries the current `attach_seq`. The server
serializes attach, input, snapshot, resize, and disconnect for each socket and
rejects events from an older attachment. Input typed while an attach frame is
being applied stays in the browser queue until that exact generation is ready.
Attach requests identify their target only with `workspace_id` and `terminal_id`;
server events use the same canonical fields.

The browser requests `GET /api/terminal-screen` only when it detects a stream
change, sequence/offset gap, unsafe alternate-buffer boundary, or a failed
xterm write. Recovery is single-flight. Output received during the request is
buffered and reconciled against the returned frame.

There is no periodic screen polling or periodic full redraw.

## Ordered Events

Normal output events contain:

- `stream_id`, `stream_epoch`, and `frame_seq`;
- `output_start` and `output_end` absolute history offsets;
- the exact raw PTY `data`.

Resize is an ordered zero-length `terminal_output` event with
`event_type: "resize"`, canonical `cols`/`rows`, and equal output offsets. This
ensures every `frame_seq` revision is observable.

A live client accepts an event only when:

1. the stream matches;
2. `frame_seq` is exactly the accepted sequence plus one;
3. `output_start` equals the accepted end offset.

Receipt and rendering are separate states. The accepted sequence advances when
an event enters the local queue; the rendered sequence advances only from the
xterm write callback. Duplicate or stale complete events are ignored. A gap is
never repaired by string overlap or ANSI substring trimming.

## Rendering Rules

- Feed live PTY data to xterm unchanged.
- Serialize all writes through one callback-driven queue.
- Drain an in-flight delta before resetting for an authoritative frame.
- Never call `reset()` for healthy output.
- Never infer terminal state from visible text.
- Never restore an interactive TUI from a truncated ANSI tail.
- Preserve the user's scroll position when follow-output is disabled.

Main-terminal geometry is fitted locally and published by the active geometry
owner. Big-screen sockets are view-only: they retain the canonical PTY grid and
scale its font for the tile instead of interpreting control sequences at a
different number of rows or columns.

## History

Terminal scrollback and Codex conversation history are separate systems.
Terminal offsets belong only to the current PTY stream. Codex history is read
from native Codex rollout files and deduplicated by the first canonical
`session_meta.payload.id` in each rollout. Local metadata maps that thread ID to
optional naming and archive/importance flags. A fork remains a separate history
item and exposes its direct source as `forked_from_id`; following those parent
IDs reconstructs a longer fork chain. Subagent threads are not shown as
conversations. History is ordered by Codex's native update time.

A terminal may persist a one-way display association to a `thread_id`:
`terminal -> thread_id -> conversation title`. Multiple terminals may point to
the same thread, and a conversation rename updates all of their displayed
titles. The association never starts, resumes, stops, or otherwise owns a Codex
process. A History resume request is idempotent per workspace and thread: the
server reuses an associated terminal when one exists, otherwise creates one,
and directly starts a standalone CLI using an argument vector under a lifecycle lock.
The browser never sends a shell resume command. For Codex started manually in a shell, the server
discovers the root rollout from the foreground process's open file descriptors;
subagent rollouts are ignored.

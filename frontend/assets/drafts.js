"use strict";

// Separate entries isolate drafts for different conversations and workspaces.
// Ephemeral conversations stay in this page's memory.
class CodexwsDraftStore {
  constructor(storage) {
    this.storage = storage;
    this.entries = new Map();
    this.unsaved = new Set();
  }

  key(context) {
    return `codexws.draft.${JSON.stringify([context.workspaceId, context.threadId])}`;
  }

  read(context) {
    const key = this.key(context);
    if (!this.entries.has(key)) {
      let value = null;
      if (!context.ephemeral) {
        try {
          value = JSON.parse(this.storage().getItem(key) || "null");
        } catch (_) {
          this.unsaved.add(key);
        }
      }
      this.entries.set(key, {
        text: typeof value?.text === "string" ? value.text : "",
        pending: Array.isArray(value?.pending)
          ? value.pending.filter(entry => typeof entry?.id === "string" && typeof entry?.text === "string")
          : [],
      });
    }
    return this.entries.get(key);
  }

  persist(context) {
    const key = this.key(context);
    const entry = this.read(context);
    if (context.ephemeral) return;
    try {
      if (entry.text || entry.pending.length) this.storage().setItem(key, JSON.stringify(entry));
      else this.storage().removeItem(key);
      this.unsaved.delete(key);
    } catch (_) {
      this.unsaved.add(key);
    }
  }

  saveText(context, text) {
    this.read(context).text = text;
    this.persist(context);
  }

  queue(context, id, text) {
    const entry = this.read(context);
    entry.pending.push({ id, text });
    entry.text = "";
    this.persist(context);
  }

  settle(context, id, failed = false) {
    const entry = this.read(context);
    const pending = entry.pending.find(item => item.id === id);
    if (!pending) return false;
    entry.pending = entry.pending.filter(item => item.id !== id);
    if (failed) entry.text = [pending.text, entry.text].filter(Boolean).join("\n");
    this.persist(context);
    return true;
  }

  recover(context) {
    const entry = this.read(context);
    entry.text = [...entry.pending.map(item => item.text), entry.text].filter(Boolean).join("\n");
    entry.pending = [];
    this.persist(context);
    return entry.text;
  }
}

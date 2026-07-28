import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { sendMessage } from "./api";
import type { ChatMessage } from "./types";
import "./LlmChatApp.css";

export function LlmChatApp() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  function scrollToBottom() {
    requestAnimationFrame(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }));
  }

  async function handleSend() {
    const content = input.trim();
    if (!content || sending) return;

    const history = [...messages, { role: "user", content } as ChatMessage];
    setMessages(history);
    setInput("");
    setError(null);
    setSending(true);
    scrollToBottom();

    try {
      const reply = await sendMessage(history);
      setMessages([...history, { role: "assistant", content: reply }]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to get a reply");
    } finally {
      setSending(false);
      scrollToBottom();
    }
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void handleSend();
    }
  }

  return (
    <div className="llm-chat">
      <Link to="/services" className="back-link">
        &larr; Back to services
      </Link>
      <h2>llm-chat</h2>

      {error && <p className="error-message">{error}</p>}

      <div className="llm-chat-messages">
        {messages.length === 0 && (
          <p className="llm-chat-empty">Say something to start the conversation.</p>
        )}
        {messages.map((message, index) => (
          <div key={index} className={`llm-chat-bubble llm-chat-bubble-${message.role}`}>
            {message.content}
          </div>
        ))}
        {sending && <div className="llm-chat-bubble llm-chat-bubble-assistant">Thinking…</div>}
        <div ref={bottomRef} />
      </div>

      <div className="llm-chat-composer">
        <textarea
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Type a message… (Enter to send, Shift+Enter for a new line)"
          rows={2}
        />
        <button onClick={() => void handleSend()} disabled={sending || !input.trim()}>
          Send
        </button>
      </div>
    </div>
  );
}

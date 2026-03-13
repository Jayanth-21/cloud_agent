"""
Minimal Streamlit frontend for the Cloud Intelligence Agent.
- Sidebar: list of chats (stored in session state; add DB later if you want).
- Main: messages + input. One API route that calls the streaming server with sessionId and streams back.
- Renders markdown and inline base64 images (e.g. from the visualization tool).
"""
import base64
import io
import re
import uuid

import streamlit as st

from streamlit_client import invoke_stream

# Config: URL of your streaming server (e.g. python agent/streaming_server.py)
import os
try:
    STREAMING_URL = st.secrets.get("STREAMING_SERVER_URL")
except Exception:
    STREAMING_URL = None
STREAMING_URL = STREAMING_URL or os.environ.get("STREAMING_SERVER_URL", "http://127.0.0.1:8080/invoke")

# Session state: chats = { "chat_id": { "title": str, "messages": [ {"role": "user"|"assistant", "content": str}, ... ] } }
if "chats" not in st.session_state:
    st.session_state.chats = {}
if "current_chat_id" not in st.session_state:
    st.session_state.current_chat_id = None


def ensure_current_chat():
    if st.session_state.current_chat_id is not None and st.session_state.current_chat_id in st.session_state.chats:
        return
    # Create a new chat
    chat_id = str(uuid.uuid4())
    st.session_state.chats[chat_id] = {"title": "New chat", "messages": []}
    st.session_state.current_chat_id = chat_id


def set_chat_title(chat_id: str, first_message: str):
    title = (first_message[:50] + "...") if len(first_message) > 50 else first_message
    if not title.strip():
        title = "New chat"
    st.session_state.chats[chat_id]["title"] = title


# --- Sidebar: list of chats ---
st.sidebar.title("Chats")
if st.sidebar.button("New chat"):
    chat_id = str(uuid.uuid4())
    st.session_state.chats[chat_id] = {"title": "New chat", "messages": []}
    st.session_state.current_chat_id = chat_id
    st.rerun()

chat_ids = list(st.session_state.chats.keys())
if not chat_ids:
    ensure_current_chat()
    chat_ids = list(st.session_state.chats.keys())

for cid in chat_ids:
    title = st.session_state.chats[cid]["title"]
    if st.sidebar.button(
        title,
        key=f"chat_{cid}",
        use_container_width=True,
    ):
        st.session_state.current_chat_id = cid
        st.rerun()

# --- Main: messages + input ---
ensure_current_chat()
chat_id = st.session_state.current_chat_id
chat = st.session_state.chats[chat_id]
messages = chat["messages"]

def render_message_content(content: str) -> None:
    """Render assistant message: markdown and inline base64 images (e.g. ![Chart](data:image/png;base64,...))."""
    if not content:
        return
    # Match markdown image; allow optional whitespace in base64 (wrapped lines)
    pattern = re.compile(
        r"!\[([^\]]*)\]\(data:image/([^;]+);base64,([A-Za-z0-9+/=\s]+)\)",
        re.DOTALL,
    )
    last_end = 0
    for m in pattern.finditer(content):
        if m.start() > last_end:
            st.markdown(content[last_end : m.start()], unsafe_allow_html=False)
        b64_raw = m.group(3).replace("\n", "").replace(" ", "").replace("\r", "")
        try:
            img_bytes = base64.b64decode(b64_raw, validate=True)
            st.image(io.BytesIO(img_bytes), caption=m.group(1) or "Chart", use_container_width=True)
        except Exception:
            st.caption("Chart image could not be displayed (data may be truncated or invalid).")
        last_end = m.end()
    if last_end < len(content):
        st.markdown(content[last_end:], unsafe_allow_html=False)


st.title("Cloud Intelligence Agent")

for msg in messages:
    with st.chat_message(msg["role"]):
        if msg["role"] == "assistant":
            render_message_content(msg.get("content") or "")
            if msg.get("clarification_needed"):
                st.caption("Please provide the information above so I can continue.")
        else:
            st.markdown(msg.get("content") or "")

if prompt := st.chat_input("Ask about cost, logs, or audit..."):
    messages.append({"role": "user", "content": prompt})
    if chat["title"] == "New chat":
        set_chat_title(chat_id, prompt)

    with st.chat_message("assistant"):
        placeholder = st.empty()
        placeholder.markdown("Agent is working...")
        full = []
        clarification_needed = False
        try:
            for chunk in invoke_stream(prompt, session_id=chat_id, url=STREAMING_URL):
                if isinstance(chunk, dict):
                    clarification_needed = chunk.get("clarification_needed", False)
                    continue
                full.append(chunk)
            # Full response is saved below and rendered on rerun via render_message_content.
        except Exception as e:
            placeholder.error(str(e))
            full = [str(e)]
        content = "".join(full)
        messages.append({
            "role": "assistant",
            "content": content,
            "clarification_needed": clarification_needed,
        })

    st.rerun()

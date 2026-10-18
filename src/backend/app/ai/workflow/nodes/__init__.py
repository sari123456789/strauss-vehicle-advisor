"""LangGraph nodes. Each node reads `ConversationState`, works on `TurnRuntime` and returns only
durable state updates. Business decisions come from tool results, never from the model."""

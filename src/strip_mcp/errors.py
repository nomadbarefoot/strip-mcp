"""All error types for strip-mcp."""


class StripError(Exception):
    """Base error for all strip-mcp errors."""


class ServerStartError(StripError):
    """Subprocess failed to start or handshake failed."""


class ServerCrashedError(StripError):
    """Subprocess died mid-session."""


class RemoteRPCError(StripError):
    """JSON-RPC error returned by a remote MCP server."""

    def __init__(self, error: object) -> None:
        self.error = error
        if isinstance(error, dict):
            code = error.get("code")
            message = error.get("message")
            data = error.get("data")
            detail = f"RPC error {code}: {message}" if code is not None else f"RPC error: {message}"
            if data is not None:
                detail = f"{detail} (data={data!r})"
            super().__init__(detail)
            return
        super().__init__(f"RPC error: {error!r}")


class ToolNotFoundError(StripError):
    """LLM requested a tool name that doesn't exist."""

    def __init__(self, name: str, suggestion: str | None = None) -> None:
        self.name = name
        self.suggestion = suggestion
        msg = f"Tool not found: {name!r}"
        if suggestion:
            msg += f". Did you mean {suggestion!r}?"
        super().__init__(msg)


class ToolCollisionError(StripError):
    """Two servers registered the same tool name (namespace=False collision)."""


class ToolExecutionError(StripError):
    """Tool call returned an error from the MCP server."""

    def __init__(self, tool_name: str, mcp_error: object) -> None:
        self.tool_name = tool_name
        self.mcp_error = mcp_error
        super().__init__(f"Tool {tool_name!r} returned error: {mcp_error}")


class ToolTimeoutError(StripError):
    """Tool call exceeded configured timeout."""

    def __init__(self, tool_name: str, timeout: float) -> None:
        self.tool_name = tool_name
        self.timeout = timeout
        super().__init__(f"Tool {tool_name!r} timed out after {timeout}s")


class SchemaFetchError(StripError):
    """get_schemas called for a non-existent tool."""

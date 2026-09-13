class Property:
    """Represents a property of a tool's parameters."""

    __slots__ = ["name", "type", "description", "required"]

    def __init__(
        self,
        name: str,
        property_type: str | None = None,
        description: str = "",
        required: bool = False,
        **kwargs,
    ):
        # Support both property_type= and legacy type= keyword usage.
        if property_type is None and "type" in kwargs:
            property_type = kwargs.pop("type")
        if kwargs:
            raise TypeError(f"Unexpected keyword arguments: {', '.join(kwargs.keys())}")

        self.name = name
        self.type = property_type or "string"
        self.description = description
        self.required = required

    @property
    def json(self):
        """Returns the JSON representation of the property."""
        return {
            "type": self.type,
            "description": self.description,
        }


class Parameters:
    """Represents the parameters of a tool, which is a collection of properties."""

    __slots__ = ["properties"]

    def __init__(self, properties: list[Property]):
        self.properties = properties

    @property
    def json(self):
        """Returns the JSON representation of the parameters."""
        return {
            "type": "object",
            "properties": {prop.name: prop.json for prop in self.properties},
            "required": [prop.name for prop in self.properties if prop.required],
        }


class Tool:
    """Represents a tool that can be executed by the agent."""

    __slots__ = ["name", "description", "parameters"]

    def __init__(self, name: str, description: str, parameters: Parameters):
        self.name = name
        self.description = description
        self.parameters = parameters

    @property
    def json_anthropic(self):
        """Schema for Anthropic's API."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": (
                self.parameters.json
                if self.parameters
                else {"type": "object", "properties": {}}
            ),
        }

    @property
    def json_openai(self):
        """Schema for OpenAI's API."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": (
                    self.parameters.json
                    if self.parameters
                    else {"type": "object", "properties": {}}
                ),
            },
        }
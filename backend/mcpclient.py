import asyncio
import json
import re
from typing import Optional, List, Dict, Any
from contextlib import AsyncExitStack
from mcp import ClientSession, Tool
from mcp.client.sse import sse_client
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

class WorkflowState:
    """Enum-like class for workflow states"""
    IDLE = "idle"
    ORG_NEEDED = "org_needed"
    PROCESS_NEEDED = "process_needed"
    EVENT_NEEDED = "event_needed"
    PAGE_TITLE_NEEDED = "page_title_needed"
    FIELDS_NEEDED = "fields_needed"
    SQL_GENERATION = "sql_generation"
    COMPLETE = "complete"

class WorkflowMemory:
    """Structured memory for form page creation workflow"""
    def __init__(self):
        self.org_id: Optional[str] = None
        self.org_name: Optional[str] = None
        self.process_id: Optional[int] = None
        self.process_name: Optional[str] = None
        self.is_new_process: bool = False
        self.event_id: Optional[int] = None
        self.page_id: Optional[int] = None
        self.page_title: Optional[str] = None
        self.page_url: Optional[str] = None
        self.group_id: int = 1
        self.fields: List[Dict[str, Any]] = []
        self.current_state: str = WorkflowState.IDLE
    
    def get_summary(self) -> str:
        """Get human-readable summary of collected data"""
        summary = [f"📋 Current State: {self.current_state}"]
        if self.org_id:
            summary.append(f"   Organization: {self.org_name} (ID: {self.org_id})")
        if self.process_id:
            status = " [NEW]" if self.is_new_process else ""
            summary.append(f"   Process: {self.process_name} (ID: {self.process_id}){status}")
        if self.event_id:
            summary.append(f"   Event ID: {self.event_id}")
        if self.page_id:
            summary.append(f"   Page ID: {self.page_id}")
        if self.page_title:
            summary.append(f"   Page: {self.page_title} (URL: {self.page_url})")
        if self.fields:
            summary.append(f"   Fields: {len(self.fields)} collected")
            for field in self.fields:
                status = "existing" if field.get("existing") else "new"
                summary.append(f"    - {field['field_id']} ({status})")
        return "\n".join(summary)

class MCPAIAssistant:
    def __init__(self, model_name: str = "mistral-sql-3k:latest"):
        self.llm = ChatOllama(model=model_name, temperature=0.7)
        self.session: Optional[ClientSession] = None
        self.tools: List[Tool] = []
        self.conversation_history = []
        self.exit_stack = AsyncExitStack()
        self.mcp_url = "http://localhost:8000/sse"
        self.memory = WorkflowMemory()

        self.allowed_tools = {
            "get_organization_by_name",
            "get_process_by_name",
            "debug_process_query",
            "get_events_for_process",
            "check_field_exists",
            "generate_page_url",
            "generate_form_page_sql",
            "validate_workflow_data",
            "get_field_validation_types",
            "get_field_display_types",
        }

    async def initialize(self):
        """Initialize MCP connection via SSE"""
        try:
            streams = await self.exit_stack.enter_async_context(
                sse_client(url=self.mcp_url)
            )
            self.session = await self.exit_stack.enter_async_context(
                ClientSession(*streams)
            )
            await self.session.initialize()

            tools_response = await self.session.list_tools()
            all_server_tools = tools_response.tools
            self.tools = [
                tool for tool in all_server_tools
                if tool.name in self.allowed_tools
            ]

            print(f" Connected to MCP server with {len(self.tools)} tools")

        except Exception as e:
            print(f" Failed to connect to MCP server: {e}")
            raise

    def _get_state_context(self) -> Dict[str, Any]:
        """Get context for current state to guide LLM"""
        state = self.memory.current_state
        
        context = {
            "current_state": state,
            "memory": self.memory.__dict__,
            "next_action": None,
            "required_tool": None,
            "prompt_instruction": None
        }
        
        if state == WorkflowState.IDLE:
            context["next_action"] = "detect_intent"
            context["prompt_instruction"] = "Greet user or ask if they want to create a form page"
        
        elif state == WorkflowState.ORG_NEEDED:
            context["next_action"] = "get_organization"
            context["required_tool"] = "get_organization_by_name"
            context["prompt_instruction"] = "Ask for organization legal name"
        
        elif state == WorkflowState.PROCESS_NEEDED:
            context["next_action"] = "get_process"
            context["required_tool"] = "get_process_by_name"
            context["prompt_instruction"] = "Ask for process name. MUST check if process exists in database."
        
        elif state == WorkflowState.EVENT_NEEDED:
            context["next_action"] = "get_events"
            context["required_tool"] = "get_events_for_process"
            context["prompt_instruction"] = "Get next available event ID for the process"
        
        elif state == WorkflowState.PAGE_TITLE_NEEDED:
            context["next_action"] = "get_page_title"
            context["required_tool"] = "generate_page_url"
            context["prompt_instruction"] = "Ask for page title"
        
        elif state == WorkflowState.FIELDS_NEEDED:
            context["next_action"] = "collect_fields"
            context["required_tool"] = "check_field_exists"
            context["prompt_instruction"] = "Ask for field IDs. User can say 'done' when finished."
        
        elif state == WorkflowState.SQL_GENERATION:
            context["next_action"] = "generate_sql"
            context["required_tool"] = "generate_form_page_sql"
            context["prompt_instruction"] = "Generate SQL statements"
        
        return context

    async def _execute_state_logic(self, user_input: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute state machine logic and return results"""
        state = self.memory.current_state
        result = {
            "state_changed": False,
            "tool_called": False,
            "tool_name": None,
            "tool_result": None,
            "next_state": state,
            "action_taken": None
        }
        
        # IDLE STATE
        if state == WorkflowState.IDLE:
            if any(phrase in user_input.lower() for phrase in ["create", "form", "page", "build", "yes"]):
                self.memory.current_state = WorkflowState.ORG_NEEDED
                result["state_changed"] = True
                result["next_state"] = WorkflowState.ORG_NEEDED
                result["action_taken"] = "workflow_started"
        
        # ORG_NEEDED STATE
        elif state == WorkflowState.ORG_NEEDED:
            if user_input.lower() not in ["yes", "ok", "sure", "proceed", "start"]:
                # Call tool
                tool_result = await self._call_tool("get_organization_by_name", {"legal_name": user_input})
                result_data = json.loads(tool_result)
                
                result["tool_called"] = True
                result["tool_name"] = "get_organization_by_name"
                result["tool_result"] = tool_result
                
                if result_data.get("found"):
                    self.memory.org_id = result_data.get("orgId")
                    self.memory.org_name = result_data.get("legalName")
                    self.memory.current_state = WorkflowState.PROCESS_NEEDED
                    result["state_changed"] = True
                    result["next_state"] = WorkflowState.PROCESS_NEEDED
                    result["action_taken"] = "organization_found"
                    print(f"[MEMORY] ✓ Stored org: {self.memory.org_name} ({self.memory.org_id})")
                else:
                    result["action_taken"] = "organization_not_found"
        
        # PROCESS_NEEDED STATE
        elif state == WorkflowState.PROCESS_NEEDED:
            if user_input.lower() not in ["ok", "next", "continue", "proceed"]:
                # User provided a process name - look it up
                tool_result = await self._call_tool("get_process_by_name", {
                    "process_name": user_input,
                    "org_id": self.memory.org_id
                })
                result_data = json.loads(tool_result)
                
                result["tool_called"] = True
                result["tool_name"] = "get_process_by_name"
                result["tool_result"] = tool_result
                
                if result_data.get("found"):
                    self.memory.process_id = int(result_data.get("processId"))
                    self.memory.process_name = result_data.get("processName")
                    self.memory.is_new_process = False
                    self.memory.current_state = WorkflowState.EVENT_NEEDED
                    result["state_changed"] = True
                    result["next_state"] = WorkflowState.EVENT_NEEDED
                    result["action_taken"] = "process_found"
                    print(f"[MEMORY] ✓ Stored process: {self.memory.process_name} (ID: {self.memory.process_id})")
                else:
                    result["action_taken"] = "process_not_found"
            else:
                result["action_taken"] = "ask_for_process_name"
        
        # EVENT_NEEDED STATE
        elif state == WorkflowState.EVENT_NEEDED:
            # Always get event ID automatically
            tool_result = await self._call_tool("get_events_for_process", {
                "process_id": self.memory.process_id,
                "org_id": self.memory.org_id
            })
            result_data = json.loads(tool_result)
            
            result["tool_called"] = True
            result["tool_name"] = "get_events_for_process"
            result["tool_result"] = tool_result
            
            if result_data.get("success"):
                self.memory.event_id = result_data.get("suggestedNextEventId")
                self.memory.page_id = self.memory.event_id
                self.memory.current_state = WorkflowState.PAGE_TITLE_NEEDED
                result["state_changed"] = True
                result["next_state"] = WorkflowState.PAGE_TITLE_NEEDED
                result["action_taken"] = "event_id_retrieved"
                print(f"[MEMORY] ✓ Stored eventId/pageId: {self.memory.event_id}")
        
        # PAGE_TITLE_NEEDED STATE
        elif state == WorkflowState.PAGE_TITLE_NEEDED:
            if user_input.lower() not in ["ok", "next", "continue", "proceed"]:
                # User provided a page title
                tool_result = await self._call_tool("generate_page_url", {"page_title": user_input})
                result_data = json.loads(tool_result)
                
                result["tool_called"] = True
                result["tool_name"] = "generate_page_url"
                result["tool_result"] = tool_result
                
                if result_data.get("success"):
                    self.memory.page_title = result_data.get("pageTitle")
                    self.memory.page_url = result_data.get("pageURL")
                    self.memory.current_state = WorkflowState.FIELDS_NEEDED
                    result["state_changed"] = True
                    result["next_state"] = WorkflowState.FIELDS_NEEDED
                    result["action_taken"] = "page_title_set"
                    print(f"[MEMORY] ✓ Stored page: {self.memory.page_title} -> {self.memory.page_url}")
            else:
                result["action_taken"] = "ask_for_page_title"
        
        # FIELDS_NEEDED STATE
        elif state == WorkflowState.FIELDS_NEEDED:
            if user_input.lower() in ["done", "finish", "complete", "no more fields"]:
                if len(self.memory.fields) == 0:
                    result["action_taken"] = "no_fields_added"
                else:
                    self.memory.current_state = WorkflowState.SQL_GENERATION
                    result["state_changed"] = True
                    result["next_state"] = WorkflowState.SQL_GENERATION
                    result["action_taken"] = "fields_complete"
                    result["field_count"] = len(self.memory.fields)
            elif user_input.lower() in ["ok", "next", "continue", "proceed"]:
                result["action_taken"] = "ask_for_fields"
            else:
                # User provided a field ID - check if it exists
                tool_result = await self._call_tool("check_field_exists", {"field_id": user_input})
                result_data = json.loads(tool_result)
                
                result["tool_called"] = True
                result["tool_name"] = "check_field_exists"
                result["tool_result"] = tool_result
                
                if result_data.get("found"):
                    # Existing field
                    self.memory.fields.append({
                        "field_id": result_data.get("fieldId"),
                        "existing": True,
                        "display_type": result_data.get("displayType"),
                        "validation_type": result_data.get("validationType")
                    })
                    result["action_taken"] = "field_added_existing"
                    result["field_count"] = len(self.memory.fields)
                    result["field_id"] = result_data.get("fieldId")
                    print(f"[MEMORY] ✓ Added existing field: {result_data.get('fieldId')}")
                else:
                    # New field - create with defaults
                    self.memory.fields.append({
                        "field_id": user_input,
                        "existing": False,
                        "display_type": "label",
                        "validation_type": "E"
                    })
                    result["action_taken"] = "field_added_new"
                    result["field_count"] = len(self.memory.fields)
                    result["field_id"] = user_input
                    print(f"[MEMORY] ✓ Added new field: {user_input}")
        
        # SQL_GENERATION STATE
        elif state == WorkflowState.SQL_GENERATION:
            # Separate existing and new fields
            existing_fields = [f for f in self.memory.fields if f.get("existing")]
            new_fields = [f for f in self.memory.fields if not f.get("existing")]
            
            form_data = {
                "org_id": self.memory.org_id,
                "org_name": self.memory.org_name,
                "process_id": self.memory.process_id,
                "process_name": self.memory.process_name,
                "is_new_process": self.memory.is_new_process,
                "event_id": self.memory.event_id,
                "page_id": self.memory.page_id,
                "page_title": self.memory.page_title,
                "page_url": self.memory.page_url,
                "event_name": self.memory.page_title,
                "group_id": self.memory.group_id,
                "is_new_group": False,
                "field_groups": [],
                "new_fields": new_fields,  # Only new fields go here
                "page_values": [
                    {
                        "field_id": field["field_id"],
                        "group_id": self.memory.group_id,
                        "field_group_id": self.memory.group_id,
                        "display_label": field["field_id"].replace("_", " ").title(),
                        "display_type": field.get("display_type", "label"),
                        "validation_type": field.get("validation_type", "E")
                    }
                    for field in self.memory.fields  # All fields go here
                ]
            }
            
            tool_result = await self._call_tool("generate_form_page_sql", {
                "form_data_json": json.dumps(form_data)
            })
            
            result["tool_called"] = True
            result["tool_name"] = "generate_form_page_sql"
            result["tool_result"] = tool_result
            result["action_taken"] = "sql_generated"
            
            self.memory.current_state = WorkflowState.COMPLETE
            result["state_changed"] = True
            result["next_state"] = WorkflowState.COMPLETE
        
        return result

    async def chat(self, user_input: str) -> str:
        """Process user input with hybrid state machine + LLM approach"""
        if not self.session:
            await self.initialize()

        # Handle special commands
        if user_input.lower() in ["show memory", "show state", "status"]:
            return self.memory.get_summary()
        
        if "process" in user_input.lower() and "id" in user_input.lower() and self.memory.process_id:
            return f"The process ID for '{self.memory.process_name}' is: {self.memory.process_id}"

        try:
            # Get current state context
            context = self._get_state_context()
            
            # Execute state machine logic (tools, state transitions)
            state_result = await self._execute_state_logic(user_input, context)
            
            # CRITICAL FIX: If SQL was generated, return it directly without LLM
            action_taken = state_result.get('action_taken')
            if action_taken == "sql_generated":
                tool_result = state_result.get('tool_result')
                if tool_result:
                    return tool_result
                else:
                    return "Error: SQL generation failed - no result returned"
            
            # Build prompt for LLM with state context
            field_id = state_result.get('field_id', 'unknown')
            # FIX: Get field_count from memory, not from state_result
            field_count = len(self.memory.fields)
            
            system_prompt = f"""You are a helpful assistant for form page creation.

CURRENT STATE: {context['current_state']}
ACTION TAKEN: {action_taken}

CRITICAL RESPONSE RULES:
- If action_taken is "workflow_started", respond: "Great! Which organization is this form page for? (Provide the legal name)"
- If action_taken is "organization_found", respond: "Found [orgName] (ID: [orgId]). Which process should this form page belong to?"
- If action_taken is "ask_for_process_name", respond: "Which process should this form page belong to? (Provide the process name)"
- If action_taken is "process_found", respond: "Found process '[processName]' (ID: [processId]). Moving to next step..."
- If action_taken is "event_id_retrieved", respond: "Event ID: [eventId] assigned. What should the page title be?"
- If action_taken is "ask_for_page_title", respond: "What should the page title be? (e.g., 'Task Details')"
- If action_taken is "page_title_set", respond: "Page '[pageTitle]' created with URL: [pageURL]. Ready to add fields. What field ID do you want to add? (or say 'done')"
- If action_taken is "ask_for_fields", respond: "What field ID do you want to add? (Provide field ID, or say 'done' to finish)"
- If action_taken is "field_added_existing", respond: "Field '{field_id}' added ({field_count} total). Add another field or say 'done'."
- If action_taken is "field_added_new", respond: "New field '{field_id}' will be created ({field_count} total). Add another field or say 'done'."
- If action_taken is "no_fields_added", respond: "You haven't added any fields yet. Please add at least one field."
- If action_taken is "fields_complete", respond: "Collected {field_count} fields. Generating SQL..."
- If action_taken is "sql_generated", output the raw SQL from tool_result without modification
- If action_taken is "organization_not_found", respond: "Organization not found. Please provide the exact legal name."
- If action_taken is "process_not_found", respond: "Process not found. Please check the process name."

TOOL RESULT:
{json.dumps(state_result.get('tool_result'), indent=2) if state_result.get('tool_result') else 'None'}

Additional context:
- field_id: {field_id}
- field_count: {field_count}

Extract exact values from tool_result and provide clear response."""

            messages = [
                SystemMessage(content=system_prompt),
                *self.conversation_history[-4:],
                HumanMessage(content=user_input)
            ]

            # Let LLM generate the response
            response = await self.llm.ainvoke(messages)
            response_text = response.content

            # Update history
            self.conversation_history.append(HumanMessage(content=user_input))
            self.conversation_history.append(AIMessage(content=response_text))

            return response_text

        except Exception as e:
            return f"Error: {str(e)}"

    async def _call_tool(self, tool_name: str, arguments: dict) -> str:
        """Call MCP tool directly"""
        try:
            result = await self.session.call_tool(tool_name, arguments)
            if result.content:
                return result.content[0].text
            return json.dumps({"success": False, "error": "No result"})
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)})

    async def close(self):
        """Close MCP connection"""
        await self.exit_stack.aclose()

async def main():
    print("=" * 60)
    print("🚀 Form Page Creation Assistant (HYBRID MODE)")
    print("=" * 60)
    assistant = MCPAIAssistant()
    await assistant.initialize()

    print("\n✅ Ready! State machine controls workflow, LLM generates responses.")
    print("💡 Type 'show memory' to see collected data")
    print("💡 Type 'quit' to exit\n")

    try:
        while True:
            user_input = input("👤 You: ")
            if user_input.lower() in ['quit', 'exit', 'q']:
                break

            if not user_input.strip():
                continue

            response = await assistant.chat(user_input)
            print(f"\n🤖 Assistant: {response}\n")
            
            # Show current state
            print(f"[State: {assistant.memory.current_state}]\n")

    finally:
        await assistant.close()

    print("\n👋 Goodbye!")

if __name__ == "__main__":
    asyncio.run(main())
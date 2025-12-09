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
    PROCESS_CREATION_CONFIRM = "process_creation_confirm"  # NEW
    EVENT_NEEDED = "event_needed"
    PAGE_TITLE_NEEDED = "page_title_needed"
    FIELDS_NEEDED = "fields_needed"
    FIELD_CREATION_CONFIRM = "field_creation_confirm"  # NEW
    FIELD_DISPLAY_TYPE = "field_display_type"  # NEW
    FIELD_VALIDATION_TYPE = "field_validation_type"  # NEW
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
        self.suggested_process_id: Optional[int] = None  # NEW
        self.event_id: Optional[int] = None
        self.page_id: Optional[int] = None
        self.page_title: Optional[str] = None
        self.page_url: Optional[str] = None
        self.group_id: int = 1
        self.fields: List[Dict[str, Any]] = []
        self.current_state: str = WorkflowState.IDLE
        
        # NEW: For field creation workflow
        self.pending_field_id: Optional[str] = None
        self.pending_display_type: Optional[str] = None
        self.pending_validation_type: Optional[str] = None

    def get_summary(self) -> str:
        """Get human-readable summary of collected data"""
        summary = [f"📋 Current State: {self.current_state}"]
        if self.org_id:
            summary.append(f"  Organization: {self.org_name} (ID: {self.org_id})")
        if self.process_id:
            status = " [NEW]" if self.is_new_process else ""
            summary.append(f"  Process: {self.process_name} (ID: {self.process_id}){status}")
        if self.event_id:
            summary.append(f"  Event ID: {self.event_id}")
        if self.page_id:
            summary.append(f"  Page ID: {self.page_id}")
        if self.page_title:
            summary.append(f"  Page: {self.page_title} (URL: {self.page_url})")
        if self.fields:
            summary.append(f"  Fields: {len(self.fields)} collected")
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
            "get_max_process_id",  # NEW: For process creation
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
            print(f"✅ Connected to MCP server with {len(self.tools)} tools")
        except Exception as e:
            print(f"❌ Failed to connect to MCP server: {e}")
            raise

    def reset(self):
        """Reset conversation state and memory - NEW METHOD"""
        self.memory = WorkflowMemory()
        self.conversation_history = []
        print("🔄 Assistant state reset - starting fresh conversation")
        
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
        elif state == WorkflowState.PROCESS_CREATION_CONFIRM:  # NEW
            context["next_action"] = "confirm_process_creation"
            context["prompt_instruction"] = "Ask if user wants to create new process"
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
        elif state == WorkflowState.FIELD_CREATION_CONFIRM:  # NEW
            context["next_action"] = "confirm_field_creation"
            context["prompt_instruction"] = "Ask if user wants to create new field"
        elif state == WorkflowState.FIELD_DISPLAY_TYPE:  # NEW
            context["next_action"] = "get_display_type"
            context["prompt_instruction"] = "Ask for display type (label/checkbox/radio/textarea/select/date)"
        elif state == WorkflowState.FIELD_VALIDATION_TYPE:  # NEW
            context["next_action"] = "get_validation_type"
            context["prompt_instruction"] = "Ask for validation type (E/N/M/NM/A/AN)"
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

        # PROCESS_NEEDED STATE - ENHANCED WITH NEW PROCESS CREATION
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
                    # EXISTING PROCESS FOUND
                    self.memory.process_id = int(result_data.get("processId"))
                    self.memory.process_name = result_data.get("processName")
                    self.memory.is_new_process = False
                    self.memory.current_state = WorkflowState.EVENT_NEEDED
                    result["state_changed"] = True
                    result["next_state"] = WorkflowState.EVENT_NEEDED
                    result["action_taken"] = "process_found"
                    print(f"[MEMORY] ✓ Stored process: {self.memory.process_name} (ID: {self.memory.process_id})")
                else:
                    # NEW: PROCESS NOT FOUND - ASK TO CREATE
                    # Get max process ID to suggest next ID
                    max_id_tool = await self._call_tool("get_max_process_id", {})
                    max_id_data = json.loads(max_id_tool)
                    
                    if max_id_data.get("success"):
                        suggested_id = max_id_data.get("suggested_next_id", 1)
                    else:
                        suggested_id = 1
                    
                    # Store for confirmation
                    self.memory.process_name = user_input
                    self.memory.suggested_process_id = suggested_id
                    
                    # Move to confirmation state
                    self.memory.current_state = WorkflowState.PROCESS_CREATION_CONFIRM
                    result["state_changed"] = True
                    result["next_state"] = WorkflowState.PROCESS_CREATION_CONFIRM
                    result["action_taken"] = "process_not_found_ask_create"
                    result["suggested_process_id"] = suggested_id
                    result["process_name"] = user_input
                    print(f"[MEMORY] Process '{user_input}' not found. Suggesting ID: {suggested_id}")
            else:
                result["action_taken"] = "ask_for_process_name"

        # NEW: PROCESS_CREATION_CONFIRM STATE
        elif state == WorkflowState.PROCESS_CREATION_CONFIRM:
            user_lower = user_input.lower().strip()
            
            if any(word in user_lower for word in ["yes", "y", "create", "ok", "sure", "proceed"]):
                # USER CONFIRMS NEW PROCESS CREATION
                self.memory.is_new_process = True
                self.memory.process_id = self.memory.suggested_process_id
                
                # Use new process ID for both event and page
                self.memory.event_id = self.memory.process_id
                self.memory.page_id = self.memory.process_id
                
                self.memory.current_state = WorkflowState.PAGE_TITLE_NEEDED
                result["state_changed"] = True
                result["next_state"] = WorkflowState.PAGE_TITLE_NEEDED
                result["action_taken"] = "new_process_confirmed"
                result["process_id"] = self.memory.process_id
                result["process_name"] = self.memory.process_name
                print(f"[MEMORY] ✓ New process confirmed: '{self.memory.process_name}' (ID: {self.memory.process_id})")
            
            elif any(word in user_lower for word in ["no", "n", "wrong", "cancel", "retry"]):
                # USER SAYS NO - PROCESS NAME WAS WRONG
                self.memory.process_name = None
                self.memory.suggested_process_id = None
                
                self.memory.current_state = WorkflowState.PROCESS_NEEDED
                result["state_changed"] = True
                result["next_state"] = WorkflowState.PROCESS_NEEDED
                result["action_taken"] = "process_name_retry"
                print(f"[MEMORY] Process name was wrong, asking again")
            else:
                # UNCLEAR RESPONSE
                result["action_taken"] = "unclear_process_response"

        # EVENT_NEEDED STATE - SKIP FOR NEW PROCESS (already set)
        elif state == WorkflowState.EVENT_NEEDED:
            # For new process, event_id and page_id are already set to process_id
            # For existing process, get next event ID
            if not self.memory.is_new_process:
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
                    print(f"[MEMORY] ✓ Stored eventId/pageId: {self.memory.event_id}")
            
            self.memory.current_state = WorkflowState.PAGE_TITLE_NEEDED
            result["state_changed"] = True
            result["next_state"] = WorkflowState.PAGE_TITLE_NEEDED
            result["action_taken"] = "event_id_retrieved"

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

        # FIELDS_NEEDED STATE - ENHANCED WITH NEW FIELD CREATION
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
                    # EXISTING FIELD
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
                    # NEW: FIELD NOT FOUND - ASK TO CREATE
                    self.memory.pending_field_id = user_input
                    
                    self.memory.current_state = WorkflowState.FIELD_CREATION_CONFIRM
                    result["state_changed"] = True
                    result["next_state"] = WorkflowState.FIELD_CREATION_CONFIRM
                    result["action_taken"] = "field_not_found_ask_create"
                    result["field_id"] = user_input
                    print(f"[MEMORY] Field '{user_input}' not found. Asking to create.")

        # NEW: FIELD_CREATION_CONFIRM STATE
        elif state == WorkflowState.FIELD_CREATION_CONFIRM:
            user_lower = user_input.lower().strip()
            
            if any(word in user_lower for word in ["yes", "y", "create", "ok", "sure"]):
                # USER CONFIRMS NEW FIELD CREATION
                self.memory.current_state = WorkflowState.FIELD_DISPLAY_TYPE
                result["state_changed"] = True
                result["next_state"] = WorkflowState.FIELD_DISPLAY_TYPE
                result["action_taken"] = "new_field_confirmed"
                print(f"[MEMORY] User confirmed creating field: {self.memory.pending_field_id}")
            
            elif any(word in user_lower for word in ["no", "n", "cancel", "skip"]):
                # USER SAYS NO - SKIP THIS FIELD
                self.memory.pending_field_id = None
                
                self.memory.current_state = WorkflowState.FIELDS_NEEDED
                result["state_changed"] = True
                result["next_state"] = WorkflowState.FIELDS_NEEDED
                result["action_taken"] = "field_creation_cancelled"
                print(f"[MEMORY] Field creation cancelled")
            else:
                # UNCLEAR RESPONSE
                result["action_taken"] = "unclear_field_response"

        # NEW: FIELD_DISPLAY_TYPE STATE
        elif state == WorkflowState.FIELD_DISPLAY_TYPE:
            valid_types = ["label", "checkbox", "radio", "textarea", "select", "date"]
            user_lower = user_input.lower().strip()
            
            if user_lower in valid_types:
                self.memory.pending_display_type = user_lower
                
                self.memory.current_state = WorkflowState.FIELD_VALIDATION_TYPE
                result["state_changed"] = True
                result["next_state"] = WorkflowState.FIELD_VALIDATION_TYPE
                result["action_taken"] = "display_type_set"
                result["display_type"] = user_lower
                print(f"[MEMORY] Display type set: {user_lower}")
            else:
                result["action_taken"] = "invalid_display_type"
                result["valid_types"] = valid_types

        # NEW: FIELD_VALIDATION_TYPE STATE
        elif state == WorkflowState.FIELD_VALIDATION_TYPE:
            # Accept any validation type code
            validation_type = user_input.strip().upper()
            self.memory.pending_validation_type = validation_type
            
            # Add complete field to memory
            self.memory.fields.append({
                "field_id": self.memory.pending_field_id,
                "existing": False,
                "display_type": self.memory.pending_display_type,
                "validation_type": self.memory.pending_validation_type
            })
            
            result["field_id"] = self.memory.pending_field_id
            result["field_count"] = len(self.memory.fields)
            
            # Clear pending data
            self.memory.pending_field_id = None
            self.memory.pending_display_type = None
            self.memory.pending_validation_type = None
            
            # Go back to collecting fields
            self.memory.current_state = WorkflowState.FIELDS_NEEDED
            result["state_changed"] = True
            result["next_state"] = WorkflowState.FIELDS_NEEDED
            result["action_taken"] = "field_added_new"
            print(f"[MEMORY] ✓ Added new field: {result['field_id']}")

        # SQL_GENERATION STATE - ENHANCED WITH NEW PROCESS/FIELD SUPPORT
        elif state == WorkflowState.SQL_GENERATION:
            # Separate existing and new fields
            existing_fields = [f for f in self.memory.fields if f.get("existing")]
            new_fields = [f for f in self.memory.fields if not f.get("existing")]

            form_data = {
                "org_id": self.memory.org_id,
                "org_name": self.memory.org_name,
                "process_id": self.memory.process_id,
                "process_name": self.memory.process_name,
                "is_new_process": self.memory.is_new_process,  # CRITICAL FLAG
                "event_id": self.memory.event_id,
                "page_id": self.memory.page_id,
                "page_title": self.memory.page_title,
                "page_url": self.memory.page_url,
                "event_name": self.memory.page_title,
                "group_id": self.memory.group_id,
                "is_new_group": False,
                "field_groups": [],
                "new_fields": new_fields,  # NEW FIELDS FOR adminFields
                "page_values": [
                    {
                        "field_id": field["field_id"],
                        "group_id": self.memory.group_id,
                        "field_group_id": self.memory.group_id,
                        "display_label": field["field_id"].replace("_", " ").title(),
                        "display_type": field.get("display_type", "label"),
                        "validation_type": field.get("validation_type", "E")
                    }
                    for field in self.memory.fields  # ALL FIELDS
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
            field_count = len(self.memory.fields)
            
            # NEW: Extended system prompt with new actions
            system_prompt = f"""You are a helpful assistant for form page creation.

CURRENT STATE: {context['current_state']}
ACTION TAKEN: {action_taken}

CRITICAL RESPONSE RULES:
- If action_taken is "workflow_started", respond: "Great! Which organization is this form page for? (Provide the legal name)"
- If action_taken is "organization_found", respond: "Found [orgName] (ID: [orgId]). Which process should this form page belong to?"
- If action_taken is "ask_for_process_name", respond: "Which process should this form page belong to? (Provide the process name)"
- If action_taken is "process_found", respond: "Found process '[processName]' (ID: [processId]). Moving to next step..."
- If action_taken is "process_not_found_ask_create", respond: "Process '[process_name]' does not exist in the database. Do you want to create a new process with ID [suggested_process_id]? (yes/no)"
- If action_taken is "new_process_confirmed", respond: "Perfect! New process '[process_name]' will be created with ID [process_id]. Event ID [process_id] and Page ID [process_id] will be used. What should the page title be?"
- If action_taken is "process_name_retry", respond: "Okay, let's try again. Which process should this form page belong to? (Provide the correct process name)"
- If action_taken is "unclear_process_response", respond: "Please respond with 'yes' to create the new process, or 'no' if the process name was incorrect."
- If action_taken is "event_id_retrieved", respond: "Event ID: [eventId] assigned. What should the page title be?"
- If action_taken is "ask_for_page_title", respond: "What should the page title be? (e.g., 'Task Details')"
- If action_taken is "page_title_set", respond: "Page '[pageTitle]' created with URL: [pageURL]. Ready to add fields. What field ID do you want to add? (or say 'done')"
- If action_taken is "ask_for_fields", respond: "What field ID do you want to add? (Provide field ID, or say 'done' to finish)"
- If action_taken is "field_added_existing", respond: "Field '{field_id}' added ({field_count} total). Add another field or say 'done'."
- If action_taken is "field_not_found_ask_create", respond: "Field '{field_id}' does not exist in adminFields table. Do you want to create a new field with this fieldId? (yes/no)"
- If action_taken is "new_field_confirmed", respond: "Great! What display type for field '{self.memory.pending_field_id}'? (label/checkbox/radio/textarea/select/date)"
- If action_taken is "display_type_set", respond: "Display type set to '[display_type]'. What validation type? (E=Email, N=Numeric, M=Mandatory, NM=Not Mandatory, A=Alphabetic, AN=Alphanumeric)"
- If action_taken is "field_added_new", respond: "New field '{field_id}' will be created ({field_count} total). Add another field or say 'done'."
- If action_taken is "field_creation_cancelled", respond: "Field creation cancelled. Please provide another field ID or type 'done' to finish."
- If action_taken is "unclear_field_response", respond: "Please respond with 'yes' to create the new field, or 'no' to skip this field."
- If action_taken is "invalid_display_type", respond: "Invalid display type. Please choose from: label, checkbox, radio, textarea, select, date"
- If action_taken is "no_fields_added", respond: "You haven't added any fields yet. Please add at least one field."
- If action_taken is "fields_complete", respond: "Collected {field_count} fields. Generating SQL..."
- If action_taken is "sql_generated", output the raw SQL from tool_result without modification
- If action_taken is "organization_not_found", respond: "Organization not found. Please provide the exact legal name."

TOOL RESULT:
{json.dumps(state_result.get('tool_result'), indent=2) if state_result.get('tool_result') else 'None'}

Additional context:
- field_id: {field_id}
- field_count: {field_count}
- process_name: {self.memory.process_name if self.memory.process_name else 'not set'}
- suggested_process_id: {state_result.get('suggested_process_id', 'N/A')}
- process_id: {state_result.get('process_id', self.memory.process_id if self.memory.process_id else 'N/A')}
- display_type: {state_result.get('display_type', 'N/A')}
- pending_field_id: {self.memory.pending_field_id if self.memory.pending_field_id else 'N/A'}

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
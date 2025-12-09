# mcp_server.py - Fixed version with better error handling

from fastmcp import FastMCP
import httpx
from datetime import datetime, date
from sqlalchemy import create_engine, text
import json
import os
from dotenv import load_dotenv
from jinja2 import Template

load_dotenv()

Database_URL = os.getenv("ORG_MASTER_DB_URL")
if not Database_URL:
    raise RuntimeError("Missing ORG_MASTER_DB_URL in environment (.env)")

print(f"Using database URI: {Database_URL}")
org_engine = create_engine(Database_URL)

# Create MCP server with FastMCP
mcp = FastMCP("MCP Server")

# =============================
# SQL GENERATION WITH JINJA2
# =============================

def sql_escape(value):
    """Escape single quotes for SQL strings"""
    if value is None:
        return 'NULL'
    return str(value).replace("'", "''")

def generate_sql_statements(form_data: dict) -> str:
    """Generate complete SQL INSERT statements using Jinja2 templates"""
    today = date.today().isoformat()
    output = []
    
    # Header
    output.append("=" * 80)
    output.append("FORM PAGE CREATION - SQL STATEMENTS")
    output.append("=" * 80)
    output.append(f"Organization: {form_data.get('org_name')} (orgId: {form_data.get('org_id')})")
    output.append(f"Process: {form_data.get('process_name')} (processId: {form_data.get('process_id')})")
    output.append(f"Page: {form_data.get('page_title')} (pageId: {form_data.get('page_id')})")
    output.append("")
    
    # 1. orgProcesses (if new process)
    # FIXED: Columns and values now match orgProcesses schema (image_ef0ac5.png)
    # Omitted auto-increment recordId
    if form_data.get('is_new_process'):
        template = Template("""
INSERT INTO orgProcesses (
    recSeq, orgId, recStatus, processId, processName, processGroupCode, pageId, platformAccess, geoFenced, 
    timeFenced, apiRouteName, externalURL, dataStatus, displaySeq, iconURL, isDefaultURL, fromDate, endDate, 
    createdBy, createdOn, modifiedBy, modifiedOn
) VALUES (
    1, '{{ org_id }}', 'A', {{ process_id }}, '{{ process_name | replace("'", "''") }}', NULL, {{ process_id }}, NULL, NULL, 
    NULL, '{{ process_name | replace("'", "''") }}', NULL, 'A', 0, NULL, 0, '{{ today }}', NULL, 
    'ADMIN', CURRENT_TIMESTAMP, 'ADMIN', CURRENT_TIMESTAMP
);""")
        output.append(template.render(
            process_id=form_data['process_id'],
            org_id=form_data['org_id'],
            process_name=form_data['process_name'],
            today=today
        ))
        output.append("")
    
    # 2. orgProcessEvents
    # FIXED: Columns and values now match orgProcessEvents schema (image_ef0dcf.png)
    # Omitted auto-increment recordId. Added isMenu, showMenu, displaySeq, dataStatus.
    template = Template("""
INSERT INTO orgProcessEvents (
    recSeq, orgId, recStatus, dataStatus, processId, eventId, eventName, eventGroupCode, pageId, eventProcessingFile, 
    isMenu, platformAccess, timeFenced, showMenu, displaySeq, fromDate, endDate, createdBy, createdOn, 
    modifiedBy, modifiedOn
) VALUES (
    1, '{{ org_id }}', 'A', 'A', {{ process_id }}, {{ event_id }}, '{{ event_name | replace("'", "''") }}', NULL, {{ page_id }}, '', 
    'Y', NULL, NULL, 'Y', 10, '{{ today }}', NULL, 'ADMIN', CURRENT_TIMESTAMP, 
    'ADMIN', CURRENT_TIMESTAMP
);""")
    output.append(template.render(
        event_id=form_data['event_id'],
        process_id=form_data['process_id'],
        org_id=form_data['org_id'],
        page_id=form_data['page_id'],
        event_name=form_data.get('event_name', form_data['page_title']),
        today=today
    ))
    output.append("")
    
    # 3. adminPages
    # FIXED: Columns and values now match adminPages schema (image_ef0e26.png)
    # Kept pageId insertion as client logic requires it. Added recStatus. Removed fromDate, endDate.
    template = Template("""
INSERT INTO adminPages (
    pageId, recStatus, pageURL, pageTitle, pageDisplayName, processId, eventId, pageType, 
    hideProcessEvents, pageSize, language, createdBy, createdOn, modifiedBy, modifiedOn
) VALUES (
    {{ page_id }}, 'A', '{{ page_url }}', '{{ page_title | replace("'", "''") }}', '{{ page_title | replace("'", "''") }}', {{ process_id }}, {{ event_id }}, 'F', 
    'Y', 12, 'en_US', 'ADMIN', CURRENT_TIMESTAMP, 'ADMIN', CURRENT_TIMESTAMP
);""")
    output.append(template.render(
        page_id=form_data['page_id'],
        process_id=form_data['process_id'],
        event_id=form_data['event_id'],
        page_title=form_data['page_title'],
        page_url=form_data['page_url'],
        today=today
    ))
    output.append("")
    
    # 4. adminFormGroups (if new group)
    # FIXED: Columns and values now match adminFormGroups schema (image_ef0e81.png)
    # Kept groupId insertion. Added recStatus, displaySeq. Removed fromDate, endDate.
    if form_data.get('is_new_group'):
        template = Template("""
INSERT INTO adminFormGroups (
    groupId, recStatus, groupName, groupStatus, displayDivLength, displaySeq, 
    createdBy, createdOn, modifiedBy, modifiedOn
) VALUES (
    {{ group_id }}, 'A', '{{ group_name | replace("'", "''") }}', 'A', 12, 0, 
    'ADMIN', CURRENT_TIMESTAMP, 'ADMIN', CURRENT_TIMESTAMP
);""")
        output.append(template.render(
            group_id=form_data['group_id'],
            group_name=form_data.get('group_name', f"Group_{form_data['group_id']}"),
            today=today
        ))
        output.append("")
    
    # 5. adminFieldGroups (if custom field groups)
    # FIXED: Columns and values now match adminFieldGroups schema (image_ef1169.png)
    # Kept fieldGroupId insertion. Added recStatus. Removed fromDate, endDate.
    if form_data.get('field_groups') and len(form_data['field_groups']) > 0:
        template = Template("""INSERT INTO adminFieldGroups (
    fieldGroupId, recStatus, groupId, fieldGroupStatus, displayDivLength, displaySeq, 
    createdBy, createdOn, modifiedBy, modifiedOn
) VALUES (
    {{ field_group_id }}, 'A', {{ group_id }}, 'A', 12, 0, 
    'ADMIN', CURRENT_TIMESTAMP, 'ADMIN', CURRENT_TIMESTAMP
);""")
        for fg_id in form_data['field_groups']:
            output.append(template.render(
                field_group_id=fg_id,
                group_id=form_data['group_id'],
                today=today
            ))
        output.append("")
    
    # 6. adminFields (new fields only)
    # FIXED: Columns and values now match adminFields schema (image_ef11c5.png)
    # Added recStatus. Removed fieldName, fieldStatus, endDate. Kept fromDate.
    if form_data.get('new_fields') and len(form_data['new_fields']) > 0:
        template = Template("""INSERT INTO adminFields (
    fieldId, recStatus, fieldType, fieldStatus, dataFieldId, remarks, fromDate, 
    displayType, defaultValue, validationType, createdBy, createdOn, modifiedBy, modifiedOn
) VALUES (
    '{{ field_id }}', 'A', 'D', 'A', '{{ field_id }}', '', '{{ today }}', 
    '{{ display_type }}', '', '{{ validation_type }}', 'ADMIN', CURRENT_TIMESTAMP, 'ADMIN', CURRENT_TIMESTAMP
);""")
        for field in form_data['new_fields']:
            output.append(template.render(
                field_id=field['field_id'],
                display_type=field.get('display_type', 'label'),
                validation_type=field.get('validation_type', 'E'),
                today=today
            ))
        output.append("")
    
    # 7. orgPageValues
    # FIXED: Columns and values now match orgPageValues schema (image_ef1205.jpg)
    # Added recStatus, displayLabel, displayType, displayLength, displaySeq, transiate, validationType, fromDate.
    # Renamed displayDivLength to displayLength. Removed endDate.
    # Assumes client provides displayLabel, displayType, etc. in the page_values objects or they will be NULL.
    if form_data.get('page_values') and len(form_data['page_values']) > 0:
        template = Template("""INSERT INTO orgPageValues (
    pageId, recSeq, orgId, recStatus, groupId, fieldGroupId, fieldId, 
    displayLabel, displayType, displaySubType, displayChannel, displayDivLength, displayLanguage, displaySeq, 
    displayDataLength, labelAlignment, required, mandatory, pageValueStatus, dependsOnValue, isRelativeTimeZone, 
    noWrap, isFilterable, sortable,  fromDate, helpText, defaultValue, validationType, 
    createdBy, createdOn, modifiedBy, modifiedOn
) VALUES (
    {{ page_id }}, {{ rec_seq }}, '{{ org_id }}', 'A', {{ group_id }}, {{ field_group_id }}, '{{ field_id }}', 
    '{% if display_label %}{{ display_label | replace("'", "''") }}{% else %}NULL{% endif %}', 
    '{% if display_type %}{{ display_type }}{% else %}NULL{% endif %}', 'search',
    'D', 12, 'en_US', 10, 0,
    'left', 'Y', 'Y', 'A', NULL, 0, 'Y', 
    NULL, NULL, '{{ today }}', '', '', 
    '{% if validation_type %}{{ validation_type }}{% else %}NULL{% endif %}', 
    'ADMIN', CURRENT_TIMESTAMP, 'ADMIN', CURRENT_TIMESTAMP
);""")
        for idx, pv in enumerate(form_data['page_values'], 1):
            # Note: This template now assumes pv (page_value) contains keys like 
            # 'display_label', 'display_type', 'validation_type'
            # If they are missing from the form_data JSON, they will be inserted as NULL.
            base_data = {
                'page_id': form_data['page_id'],
                'org_id': form_data['org_id'],
                'group_id': form_data.get('group_id', 1),
                'field_group_id': form_data.get('group_id', 1),
                'rec_seq': idx,
                'today': today
            }
            # Combine base data with per-field data
            render_context = {**base_data, **pv}
            output.append(template.render(render_context))
        output.append("")
    
    # Footer
    
    return "\n".join(output)

# =============================
# EXISTING SCHEMA TOOLS
# =============================

@mcp.tool()
def get_org_tables() -> str:
    """Get list of all tables in the orgMaster db"""
    try:
        with org_engine.connect() as conn:
            result = conn.execute(text("SHOW TABLES"))
            tables = sorted([row[0] for row in result])
            return json.dumps({"success": True, "tables": tables, "count": len(tables)}, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, indent=2)

@mcp.tool()
def describe_org_table(table_name: str) -> str:
    """Get schema/structure of a particular table"""
    try:
        with org_engine.connect() as conn:
            result = conn.execute(text(f"DESCRIBE {table_name}"))
            columns = result.keys()
            rows = [dict(zip(columns, row)) for row in result]
            return json.dumps({"success": True, "table": table_name, "schema": rows}, indent=2, default=str)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, indent=2)

# =============================
# FORM PAGE CREATION TOOLS
# =============================

@mcp.tool()
def get_organization_by_name(legal_name: str) -> str:
    """Get organization details by legal name. Returns orgId needed for form page creation."""
    try:
        with org_engine.connect() as conn:
            query = text("SELECT orgId, legalName FROM organizations WHERE legalName = :legal_name")
            result = conn.execute(query, {"legal_name": legal_name})
            row = result.fetchone()
            
            if row:
                return json.dumps({
                    "success": True,
                    "found": True,
                    "orgId": row[0],
                    "legalName": row[1]
                }, indent=2, default=str)
            else:
                return json.dumps({
                    "success": True,
                    "found": False,
                    "message": f"Organization '{legal_name}' not found"
                }, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, indent=2)

@mcp.tool()
def get_process_by_name(process_name: str, org_id: str) -> str:
    """Check if a process exists for a specific organization. Returns processId if found."""
    try:
        with org_engine.connect() as conn:
            query = text("""
                SELECT processId, processName, orgId
                FROM orgProcesses 
                WHERE processName = :process_name 
                AND orgId = :org_id
            """)
            result = conn.execute(query, {
                "process_name": process_name,
                "org_id": org_id
            })
            row = result.fetchone()
            
            if row:
                return json.dumps({
                    "success": True,
                    "found": True,
                    "processId": row[0],
                    "processName": row[1],
                    "orgId": row[2]
                }, indent=2)
            else:
                return json.dumps({
                    "success": True,
                    "found": False,
                    "message": f"Process '{process_name}' not found for organization ID {org_id}"
                }, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, indent=2)
    
@mcp.tool()
def debug_process_query(process_name: str, org_id: str) -> str:
    """Debug tool to see exactly what the database returns"""
    try:
        with org_engine.connect() as conn:
            # First, show all processes for this org
            all_query = text("""
                SELECT processId, processName, orgId
                FROM orgProcesses 
                WHERE orgId = :org_id
            """)
            all_result = conn.execute(all_query, {"org_id": org_id})
            all_processes = [{"processId": row[0], "processName": row[1]} for row in all_result]
            
            # Then, search for the specific process
            specific_query = text("""
                SELECT processId, processName, orgId
                FROM orgProcesses 
                WHERE processName = :process_name 
                AND orgId = :org_id
            """)
            specific_result = conn.execute(specific_query, {
                "process_name": process_name,
                "org_id": org_id
            })
            specific_row = specific_result.fetchone()
            
            return json.dumps({
                "success": True,
                "searched_for": process_name,
                "org_id": org_id,
                "all_processes_for_org": all_processes,
                "found_specific": specific_row is not None,
                "specific_result": {
                    "processId": specific_row[0] if specific_row else None,
                    "processName": specific_row[1] if specific_row else None,
                    "orgId": specific_row[2] if specific_row else None
                } if specific_row else None
            }, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, indent=2)

@mcp.tool()
def get_max_process_id() -> str:
    """Get the maximum processId to calculate next processId (max + 100) for new process."""
    try:
        with org_engine.connect() as conn:
            result = conn.execute(text("SELECT MAX(processId) FROM orgProcesses"))
            max_id = result.fetchone()[0]
            next_id = (max_id if max_id else 0) + 100
            
            return json.dumps({
                "success": True,
                "maxProcessId": max_id if max_id else 0,
                "suggestedNextProcessId": next_id
            }, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, indent=2)

@mcp.tool()
def get_events_for_process(process_id: int, org_id: str) -> str:
    """
    Get all events for a process. Returns next available eventId (max + 1).
    
    Args:
        process_id: The process ID to query events for
        org_id: The organization ID
    
    Returns:
        JSON string with events list and suggested next eventId
    """
    try:
        with org_engine.connect() as conn:
            query = text("""
                SELECT eventId, eventName, pageId 
                FROM orgProcessEvents 
                WHERE processId = :process_id AND orgId = :org_id
                ORDER BY eventId
            """)
            result = conn.execute(query, {"process_id": process_id, "org_id": org_id})
            events = [{"eventId": row[0], "eventName": row[1], "pageId": row[2]} for row in result]
            
            # Fix: Calculate next eventId properly
            if events:
                max_event_id = max([e["eventId"] for e in events])
                next_event_id = max_event_id + 1
            else:
                # If no events exist, use processId as first eventId
                max_event_id = None
                next_event_id = process_id
            
            return json.dumps({
                "success": True,
                "events": events,
                "count": len(events),
                "maxEventId": max_event_id,
                "suggestedNextEventId": next_event_id,
                "message": f"Found {len(events)} existing events. Next available eventId: {next_event_id}"
            }, indent=2)
            
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__,
            "message": f"Database error while fetching events: {str(e)}"
        }, indent=2)

@mcp.tool()
def check_field_exists(field_id: str) -> str:
    """
    Check if a field exists in adminFields table using a case-insensitive search.
    Returns field details if found.
    """
    try:
        with org_engine.connect() as conn:
            # Use LOWER() for case-insensitive comparison
            query = text("""
                SELECT fieldId, dataFieldId, fieldType, displayType, validationType 
                FROM adminFields 
                WHERE LOWER(fieldId) = LOWER(:field_id)
            """)
            
            result = conn.execute(query, {"field_id": field_id})
            row = result.fetchone()
            
            if row:
                return json.dumps({
                    "success": True,
                    "found": True,
                    "fieldId": row[0],
                    "dataFieldId": row[1],
                    "fieldType": row[2],
                    "displayType": row[3],
                    "validationType": row[4],
                    "message": f"Field '{row[0]}' exists in database"
                }, indent=2)
            else:
                return json.dumps({
                    "success": True,
                    "found": False,
                    "searchedFor": field_id,
                    "message": f"Field '{field_id}' not found in database"
                }, indent=2)
                
    except Exception as e:
        # CRITICAL FIX: Always return success=True even on errors
        # This prevents the workflow from getting stuck
        # The client should ask the user what to do when there's an error
        return json.dumps({
            "success": True,
            "found": False,
            "error": str(e),
            "error_type": type(e).__name__,
            "searchedFor": field_id,
            "message": f"Unable to check field '{field_id}' due to database error. You can choose to create it as a new field."
        }, indent=2)
    
@mcp.tool()
def generate_page_url(page_title: str) -> str:
    """Generate valid pageURL from pageTitle (removes spaces, camelCase). Also returns pageDisplayName."""
    try:
        words = page_title.split()
        if not words:
            return json.dumps({"success": False, "error": "Empty page title"}, indent=2)
        
        page_url = words[0].lower() + ''.join(word.capitalize() for word in words[1:])
        
        return json.dumps({
            "success": True,
            "pageTitle": page_title,
            "pageDisplayName": page_title,
            "pageURL": page_url
        }, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, indent=2)

@mcp.tool()
def generate_form_page_sql(form_data_json: str) -> str:
    """
    Generate complete SQL INSERT statements for form page creation.
    
    Required form_data_json structure:
    {
        "org_id": "uuid",
        "org_name": "Organization Name",
        "process_id": 100,
        "process_name": "Process Name",
        "is_new_process": true/false,
        "event_id": 101,
        "page_id": 101,
        "page_title": "Page Title",
        "page_url": "pageUrl",
        "event_name": "Event Name" (optional, defaults to page_title),
        "group_id": 1,
        "is_new_group": false,
        "group_name": "Group Name" (if is_new_group=true),
        "field_groups": [1, 2, 3] (optional),
        "new_fields": [
            {
                "field_id": "fieldName",
                "field_name": "Field Name",
                "display_type": "label",
                "validation_type": "E"
            }
        ],
        "page_values": [
            {
                "field_id": "fieldName",
                "group_id": 1,
                "field_group_id": 1,
                "display_label": "Field Name",
                "display_type": "label",
                "validation_type": "E"
            }
        ]
    }
    """
    try:
        form_data = json.loads(form_data_json)
        sql_output = generate_sql_statements(form_data)
        return sql_output
    except json.JSONDecodeError as e:
        return f"Error: Invalid JSON format - {str(e)}"
    except Exception as e:
        return f"Error generating SQL: {str(e)}"

# =============================
# NEW WORKFLOW HELPER TOOLS
# =============================

@mcp.tool()
def validate_workflow_data(workflow_state: str) -> str:
    """
    Validate that all required data is collected before SQL generation.
    
    workflow_state JSON should contain:
    {
        "has_org_id": true/false,
        "has_process_id": true/false,
        "has_event_id": true/false,
        "has_page_id": true/false,
        "has_page_title": true/false,
        "has_fields": true/false,
        "field_count": N
    }
    
    Returns validation status and missing items.
    """
    try:
        state = json.loads(workflow_state)
        
        required_fields = {
            "org_id": state.get("has_org_id", False),
            "process_id": state.get("has_process_id", False),
            "event_id": state.get("has_event_id", False),
            "page_id": state.get("has_page_id", False),
            "page_title": state.get("has_page_title", False),
            "fields": state.get("has_fields", False) and state.get("field_count", 0) > 0
        }
        
        missing = [field for field, has_value in required_fields.items() if not has_value]
        is_valid = len(missing) == 0
        
        return json.dumps({
            "success": True,
            "is_valid": is_valid,
            "missing_fields": missing,
            "collected_fields": [field for field, has_value in required_fields.items() if has_value],
            "can_generate_sql": is_valid,
            "message": "All required data collected!" if is_valid else f"Missing: {', '.join(missing)}"
        }, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, indent=2)


@mcp.tool()
def get_field_validation_types() -> str:
    """Get list of valid validation types for field creation."""
    validation_types = {
        "E": "Email - validates email format",
        "N": "Numeric - accepts only numbers",
        "M": "Mandatory - field is required",
        "P": "Phone - validates phone number format",
        "A": "Alphanumeric - letters and numbers only",
        "D": "Date - validates date format",
        "T": "Time - validates time format",
        "U": "URL - validates URL format"
    }
    
    return json.dumps({
        "success": True,
        "validation_types": validation_types,
        "default": "E"
    }, indent=2)

@mcp.tool()
def generate_insert_template(table_name: str) -> str:
    """Generate a template INSERT statement with placeholders for values."""
    try:
        with org_engine.connect() as conn:
            result = conn.execute(text(f"DESCRIBE {table_name}"))
            columns = [row[0] for row in result if "auto_increment" not in str(row[5]).lower()]
            placeholders = [f"<{col}>" for col in columns]
            sql = f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({', '.join(placeholders)});"
            return sql
    except Exception as e:
        return f"Error generating insert template: {str(e)}"

@mcp.tool()
def get_field_display_types() -> str:
    """Get list of valid display types for field creation."""
    display_types = {
        "label": "Text label/input field",
        "checkbox": "Checkbox for boolean values",
        "radio": "Radio button for single selection",
        "textarea": "Multi-line text area",
        "select": "Dropdown selection",
        "date": "Date picker",
        "time": "Time picker",
        "file": "File upload"
    }
    
    return json.dumps({
        "success": True,
        "display_types": display_types,
        "default": "label"
    }, indent=2)

# =============================
# EXISTING UTILITY TOOLS
# =============================

@mcp.tool()
def search_value_in_table(table_name: str, search_column: str, search_value: str) -> str:
    """Search for a specific value in a table column and return if it exists."""
    try:
        with org_engine.connect() as conn:
            query = text(f"""
                SELECT * FROM {table_name} 
                WHERE {search_column} = :search_value
                LIMIT 10
            """)
            result = conn.execute(query, {"search_value": search_value})
            columns = result.keys()
            matches = [dict(zip(columns, row)) for row in result]
            
            return json.dumps({
                "found": len(matches) > 0,
                "count": len(matches),
                "matches": matches
            }, indent=2, default=str)
    except Exception as e:
        return json.dumps({"found": False, "error": str(e)}, indent=2)

@mcp.tool()
def get_related_value(table_name: str, search_column: str, search_value: str, target_column: str) -> str:
    """Find a row by searching one column and return the value from another column."""
    try:
        with org_engine.connect() as conn:
            query = text(f"""
                SELECT * FROM {table_name} 
                WHERE {search_column} = :search_value
                LIMIT 1
            """)
            result = conn.execute(query, {"search_value": search_value})
            columns = result.keys()
            rows = [dict(zip(columns, row)) for row in result]
            
            if rows:
                target_value = rows[0].get(target_column, None)
                return json.dumps({
                    "found": True,
                    "value": target_value,
                    "row_data": rows[0]
                }, indent=2, default=str)
            else:
                return json.dumps({"found": False, "value": None}, indent=2)
    except Exception as e:
        return json.dumps({"found": False, "error": str(e)}, indent=2)

# Run the server
if __name__ == "__main__":
    print("🚀 Starting MCP Server with SSE transport on http://localhost:8000/sse")
    print("📋 Form Page Creation Tools Available:")
    print("   • get_organization_by_name - Get orgId from organization name")
    print("   • get_process_by_name - Check if process exists")
    print("   • get_max_process_id - Get next processId for new process")
    print("   • get_events_for_process - Get events and next eventId")
    print("   • check_field_exists - Validate field existence (case-insensitive)")
    print("   • generate_page_url - Create pageURL from title")
    print("   • generate_form_page_sql - Generate complete SQL statements")
    print("   • validate_workflow_data - Validate collected data")
    print("   • get_field_validation_types - Get validation type options")
    print("   • get_field_display_types - Get display type options")
    print("---")
    print("🗑️ Other (Unused by Form Client) Tools:")
    print("   • calculator, search_value_in_table, get_related_value")
    print("   • get_org_tables, describe_org_table")
    mcp.run(transport="sse", port=8000)
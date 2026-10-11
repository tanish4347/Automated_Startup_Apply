import re
from datetime import date
from typing import Any, List, Optional, Dict

class UserInputRequired(Exception):
    pass

class RejectAnswer(Exception):
    pass

def normalize_answer(value: Any, expected_type: str, options: Optional[List[str]] = None) -> Any:
    """Normalize the raw answer to the expected_type."""
    if value is None:
        return None
        
    if (expected_type == "enum" or expected_type in ("select", "radio", "combobox")) and options:
        return _match_enum(value, options)
    
    if expected_type == "number":
        return _extract_number(value)
        
    if expected_type == "boolean":
        return _extract_boolean(value, options)
        
    if expected_type == "multi-select" and options:
        return _match_multi_select(value, options)
        
    if expected_type == "date":
        return _extract_date(value)
        
    if expected_type == "string":
        if isinstance(value, dict) or isinstance(value, list):
            raise RejectAnswer(f"cannot pass structured data to string field: {value}")
        return str(value).strip()
        
    return value

def _match_enum(value: Any, options: List[str]) -> str:
    # If dict with 'start' and 'end', we might be matching a year
    if isinstance(value, dict):
        if "end" in value:
            v_str = str(value["end"])[:4]
        elif "start" in value:
            v_str = str(value["start"])[:4]
        else:
            v_str = str(value)
    else:
        v_str = str(value).strip()
        
    v_lower = v_str.lower()
    
    # 1. Exact match
    for o in options:
        if o.strip().lower() == v_lower:
            return o
            
    # 2. Boolean prefix (Yes, No)
    for o in options:
        ol = o.strip().lower()
        if v_lower in ("yes", "no") and (ol == v_lower or ol.startswith(v_lower + ",") or ol.startswith(v_lower + " ")):
            return o
            
    # 3. Substring match
    hits = [o for o in options if v_lower and (v_lower in o.lower() or o.lower() in v_lower)]
    
    if len(hits) == 1:
        return hits[0]
    elif len(hits) > 1:
        raise UserInputRequired("ambiguous option match")
        
    raise UserInputRequired("no matching option found")

def _extract_number(value: Any) -> str:
    if isinstance(value, dict):
        if "end" in value:
            v = str(value["end"])[:4]
        elif "start" in value:
            v = str(value["start"])[:4]
        else:
            v = str(value.get("value", value))
    else:
        v = str(value)
        
    num = re.search(r'-?\d+(\.\d+)?', v)
    if num:
        return num.group(0)
    raise RejectAnswer(f"cannot extract number from {value}")

def _extract_boolean(value: Any, options: Optional[List[str]]) -> str | bool:
    v_str = str(value).strip().lower()
    is_true = v_str in ("yes", "true", "1", "y")
    is_false = v_str in ("no", "false", "0", "n")
    
    if not (is_true or is_false):
        raise RejectAnswer(f"cannot extract boolean from {value}")
        
    if options:
        # Match against options (e.g. Yes/No or True/False)
        target = "yes" if is_true else "no"
        for o in options:
            if o.strip().lower() == target:
                return o
        target = "true" if is_true else "false"
        for o in options:
            if o.strip().lower() == target:
                return o
                
    return True if is_true else False

def _match_multi_select(value: Any, options: List[str]) -> List[str]:
    if not isinstance(value, (list, tuple)):
        # Try split by comma
        value = [v.strip() for v in str(value).split(",") if v.strip()]
        
    matched = []
    for v in value:
        try:
            m = _match_enum(v, options)
            if m not in matched:
                matched.append(m)
        except UserInputRequired:
            pass
            
    if not matched:
        raise UserInputRequired("no matching options found for multi-select")
    return matched

def _extract_date(value: Any) -> str:
    if isinstance(value, dict):
        if "start" in value:
            return str(value["start"])
        if "end" in value:
            return str(value["end"])
    # Just return string representation for date fields for now
    return str(value).strip()

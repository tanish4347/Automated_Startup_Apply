import re
from typing import Any
import datetime

class TypeValidationError(Exception):
    pass

class NeedsUserInputError(Exception):
    pass

def adapt_value_to_type(value: Any, field_type: str, control: str | None = None) -> Any:
    """Ensure the value is strictly appropriate for the field type."""
    if value is None:
        return None

    is_numeric = control == "number" or field_type == "number"
    
    if is_numeric:
        # Date object to year if it represents a year
        if isinstance(value, dict):
            if "end" in value:
                value = str(value["end"])[:4]
            elif "start" in value:
                value = str(value["start"])[:4]
            else:
                value = str(value)
        
        val_str = str(value)
        if val_str.isdigit():
            return val_str
            
        num = re.search(r'\d+', val_str)
        if num:
            return num.group(0)
        raise TypeValidationError(f"Cannot cast {value} to number")

    if control in ("checkbox", "radio"):
        if isinstance(value, bool):
            return value
        val_str = str(value).lower()
        if val_str in ("yes", "true", "1", "y"):
            return True
        if val_str in ("no", "false", "0", "n"):
            return False
        return value # string options

    if field_type == "file":
        return str(value)

    if isinstance(value, dict):
        if "amount" in value:
            return str(value["amount"])
        if "start" in value or "end" in value:
             return f"{value.get('start', '')} - {value.get('end', '')}"
        return str(value)
        
    return str(value)


def match_dropdown_option(candidate: Any, options: list[str]) -> str | None:
    """Strict option matching hierarchy. Exact -> Normalized -> Synonym -> Fuzzy."""
    if not options:
        return None
        
    candidate_str = str(candidate).strip()
    candidate_lower = candidate_str.lower()
    
    # 1. Exact
    if candidate_str in options:
        return candidate_str
        
    # 2. Normalized Exact
    options_lower = {opt.lower(): opt for opt in options}
    if candidate_lower in options_lower:
        return options_lower[candidate_lower]
        
    # 3. Deterministic Synonym/Alias (Simple mappings)
    synonyms = {
        "usa": "united states",
        "united states of america": "united states",
        "us": "united states",
        "uk": "united kingdom",
        "bachelors": "bachelor's degree",
        "masters": "master's degree",
        "phd": "doctorate"
    }
    
    mapped_candidate = synonyms.get(candidate_lower, candidate_lower)
    if mapped_candidate in options_lower:
        return options_lower[mapped_candidate]
        
    for opt in options:
        opt_lower = opt.lower()
        mapped_opt = synonyms.get(opt_lower, opt_lower)
        if mapped_candidate == mapped_opt:
            return opt

    # 3b. Binary Affirmation / Negation for Yes/No dropdowns
    affirmatives = {"yes", "yeah", "yep", "sure", "definitely", "true", "y", "yeah i would", "i would", "yes i would", "yes i do", "i do", "yes please"}
    negatives = {"no", "nah", "nope", "never", "false", "n", "i would not", "no i would not", "i don't", "no i do not"}
    if candidate_lower in affirmatives and "yes" in options_lower:
        return options_lower["yes"]
    if candidate_lower in negatives and "no" in options_lower:
        return options_lower["no"]

    # 3c. Consent and agreement options (e.g. "Confirm" -> "Yes - I consent...")
    if candidate_lower in ("confirm", "i agree", "agree", "consent", "i confirm"):
        consent_opts = [
            opt for opt in options
            if re.search(r"\b(yes|consent|agree|confirm)\b", opt, re.I)
            and not re.search(r"\b(no|do not|don't|not consent)\b", opt, re.I)
        ]
        if len(consent_opts) == 1:
            return consent_opts[0]

    # 4. Fuzzy Similarity (Simple substring)
    matches = []
    for opt in options:
        opt_l = opt.lower()
        if candidate_lower in opt_l or opt_l in candidate_lower:
            matches.append(opt)
              
    if len(matches) == 1:
        return matches[0]
    elif len(matches) > 1:
        raise NeedsUserInputError("ambiguous option match")
        
    return None

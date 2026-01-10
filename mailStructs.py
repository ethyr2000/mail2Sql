"""
This module defines the data structures used throughout the application,
primarily for type hinting and ensuring data consistency. It is organized into
four main sections:

1.  **Core Contact Management:** Data models for contacts, email addresses,
    phone numbers, and physical addresses, representing the foundational
    entities in the database.
2.  **Email & Message Data:** Data models for storing parsed email information,
    including the main email content, attachments, headers, labels, and
    authentication results.
3.  **API Extraction Input Format:** Typed dictionaries that represent the
    structure of data as it is extracted from the Gmail API, before it is
    transformed and inserted into the database.
4.  **Utility & Result Types:** Helper types and data structures for specific
    use cases, such as keyword lists and database operation results.

Each `TypedDict` corresponds to a table in the database or a specific data
format used in the application's logic.
"""
from typing import TypedDict, List, Optional, Dict, Any, Tuple
import datetime

# --- Utility Types ---

class ProximityScores(TypedDict):
    """Represents the proximity fields stored in the contacts table."""
    family_proximity: int
    physical_proximity: int
    business_proximity: int
    digital_proximity: int
    interest_proximity: int
    church_proximity: int

class KeywordDict(TypedDict):
    """Represents keywords often stored as JSON arrays in database columns."""
    keywords: List[str]

class AdditionalPart(TypedDict):
    """Represents a part of a multipart message that is not otherwise handled."""
    part_id: str
    mime_type: str
    filename: Optional[str]
    size: int
    
# ==============================================================================
# --- 1. CORE CONTACT MANAGEMENT ---
# ==============================================================================

class ContactModel(TypedDict):
    """
    Data model for the 'contacts' table. Represents an individual person.
    """
    contact_id: Optional[int] # Primary Key
    
    # --- Identity ---
    first_name: Optional[str]
    last_name: Optional[str]
    common_name: Optional[str] # Full name for display purposes
    
    # --- Keywords & Relationship Info (Stored as JSON in DB) ---
    interest_keywords: Optional[List[str]]
    family_members: Optional[List[str]]
    church: Optional[str]
    employer: Optional[str]

    # --- Proximity Scores ---
    family_proximity: int
    physical_proximity: int
    business_proximity: int
    digital_proximity: int
    interest_proximity: int
    church_proximity: int

class EmailAddressModel(TypedDict):
    """
    Data model for the 'email_address' table. Each entry is a unique email address.
    """
    email: str # Primary Key
    display_name: Optional[str]
    contact_id: int # Foreign Key to ContactModel, linking an email to a person.
    
    # --- Keywords (Stored as JSON in DB) ---
    interest_keywords: Optional[List[str]]
    business_keywords: Optional[List[str]]

    # --- Boolean Classification Flags ---
    # These flags categorize the nature and context of the email address.
    is_unknown_email: int
    is_personal: int
    is_business: int
    is_marketing: int
    is_membership: int
    is_family: int
    is_hobby: int
    is_retail: int
    is_education: int
    is_certification: int
    is_spam: int
    is_invalid: int
    is_interest: int
    is_mentor: int
    is_colleague: int
    is_professional: int
    is_medical: int
    is_financial: int
    
    # --- Source Flags ---
    # Indicate where this email address was first discovered.
    fromGmailHistory: int
    fromContactList: int


class ContactPhoneModel(TypedDict):
    """Data model for the 'contact_phones' table."""
    phone_number: str # Primary Key
    contact_id: int   # Foreign Key to ContactModel
    phone_type: str   # e.g., 'mobile', 'work', 'home'


class ContactAddressModel(TypedDict):
    """Data model for the 'contact_addresses' table."""
    address_id: Optional[int] # Auto-incrementing Primary Key
    contact_id: int           # Foreign Key to ContactModel
    street: Optional[str]
    city: Optional[str]
    state_province: Optional[str]
    postal_code: Optional[str]
    country: Optional[str]
    address_type: str         # e.g., 'home', 'business', 'mailing'


# ==============================================================================
# --- 2. EMAIL & MESSAGE DATA ---
# ==============================================================================

# A tuple representing a recipient: (Display Name, Email Address)
RecipientTuple = Tuple[str, str]

class EmailAuthenticationModel(TypedDict, total=False):
    """
    Data model for the 'email_authentication' table.
    Stores SPF, DKIM, and DMARC results for a message.
    """
    auth_id: Optional[int] # Auto-incrementing Primary Key
    message_id: str        # Foreign Key to EmailModel
    
    # --- SPF (Sender Policy Framework) ---
    spf_status: str        # e.g., 'pass', 'fail', 'softfail', 'none'
    spf_domain: Optional[str]
    
    # --- DKIM (DomainKeys Identified Mail) ---
    dkim_status: str       # e.g., 'pass', 'fail', 'neutral', 'none'
    dkim_domain: Optional[str]
    dkim_selector: Optional[str]
    
    # --- DMARC (Domain-based Message Authentication) ---
    dmarc_status: str      # e.g., 'pass', 'fail', 'policy_none', 'policy_quarantine'
    dmarc_policy: Optional[str] # The policy applied by DMARC (e.g., 'reject')

class EmailRoutingHeaderModel(TypedDict):
    """
    Data model for the 'email_routing_headers' table.
    Stores sequential 'Received:' headers to trace an email's path.
    """
    route_id: Optional[int] # Auto-incrementing Primary Key
    message_id: str         # Foreign Key to EmailModel
    header_name: str        # Typically 'Received'
    header_value: str       # The full content of the routing header
    hop_order: int          # The sequence, where 1 is the final hop.

class EmailModel(TypedDict):
    """
    Main data model for the 'emails' table, storing core message content.
    """
    message_id: str # Primary Key (from Gmail API)
    thread_id: str
    
    # --- Sender ---
    sender_email: str # Foreign Key to EmailAddressModel
    
    # --- Metadata & Content ---
    subject: Optional[str]
    body_text: Optional[str]
    body_html: Optional[str]
    sent_timestamp: Optional[datetime.datetime]
    
    # --- Timestamps & Technical Info ---
    internal_date_ms: int # BIGINT, from Gmail API
    date_received: Optional[str]
    mime_type: Optional[str]
    content_transfer_encoding: Optional[str]
    charset: Optional[str]             # e.g., 'UTF-8', 'ISO-8859-1'

    # --- Denormalized Recipients (Stored as JSON in DB) ---
    # For quick lookups without joins.
    to_recipients: Optional[List[str]]
    cc_recipients: Optional[List[str]]
    bcc_recipients: Optional[List[str]]

    # --- Sender Origin Headers ---
    return_path: Optional[str]
    header_sender: Optional[str]


class EmailAttachmentModel(TypedDict):
    """Data model for the 'email_attachments' table."""
    attachment_id: Optional[int] # Auto-incrementing Primary Key
    message_id: str              # Foreign Key to EmailModel
    filename: str
    mime_type: Optional[str]
    attachment_size: Optional[int]


class EmailXHeaderModel(TypedDict):
    """
    Data model for the 'email_xheaders' table.
    Stores custom or non-standard headers (those starting with 'X-').
    """
    xheader_id: Optional[int] # Auto-incrementing Primary Key
    message_id: str           # Foreign Key to EmailModel
    header_name: str
    header_value: str


class EmailLabelModel(TypedDict):
    """
    Data model for the 'email_labels' table, linking messages to Gmail labels.
    """
    label_id: Optional[int] # Auto-incrementing Primary Key
    message_id: str         # Foreign Key to EmailModel
    label_name: str


# ==============================================================================
# --- 3. API EXTRACTION INPUT FORMAT ---
# ==============================================================================

class MessageMetadata(TypedDict):
    """Minimal data structure for listing messages from the Gmail API."""
    id: str
    threadId: str
    snippet: str

class ExtractedEmailData(TypedDict):
    """
    Intermediate data structure used after parsing data from the Gmail API,
    but before it is inserted into the database. This structure is more
    flexible to handle the raw format from the API.
    """
    # --- Core Fields (mostly align with EmailModel) ---
    message_id: str 
    thread_id: str
    sender_email: str 
    subject: Optional[str]
    body_text: Optional[str]
    body_html: Optional[str]
    sent_timestamp: Optional[datetime.datetime]
    internal_date_ms: int 
    date_received: Optional[str]
    mime_type: Optional[str]
    content_transfer_encoding: Optional[str]

    # --- Fields requiring transformation before DB insertion ---
    to_recipients: List[RecipientTuple] 
    cc_recipients: List[RecipientTuple]
    bcc_recipients: List[RecipientTuple]
    
    # --- Additional parsed fields ---
    sender: Optional[str] # The raw 'From' header
    sender_name: Optional[str]
    snippet: str 
    raw_source: Optional[str] # The full, raw email source
    return_path: Optional[str]
    header_sender: Optional[str]

    # --- Related data models to be inserted into other tables ---
    attachments: List[EmailAttachmentModel]
    xheaders: List[EmailXHeaderModel]
    labels: List[EmailLabelModel]
    authentication_results: Optional[EmailAuthenticationModel]
    routing_headers: List[EmailRoutingHeaderModel]
    additional_parts: List[AdditionalPart]


# ==============================================================================
# --- 4. Database Result Type ---
# ==============================================================================

class DBSaveResult(TypedDict):
    """
    Standardized result format for database save operations.
    """
    success: bool
    document_id: str
    collection: str

# --- Publicly exposed types for import ---
__all__ = [
    "ProximityScores", "KeywordDict", "ContactModel", "EmailAddressModel", 
    "ContactPhoneModel", "ContactAddressModel", "RecipientTuple", 
    "EmailAuthenticationModel", "EmailRoutingHeaderModel", "EmailModel", 
    "EmailAttachmentModel", "EmailXHeaderModel", "EmailLabelModel", 
    "MessageMetadata", "ExtractedEmailData", "DBSaveResult", "AdditionalPart"
]
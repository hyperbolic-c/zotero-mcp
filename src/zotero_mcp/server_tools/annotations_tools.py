"""Annotation tools for Zotero MCP."""

import os
import uuid
import tempfile
from typing import Any

from fastmcp import Context, FastMCP

from zotero_mcp.client import get_zotero_client, get_local_zotero_client, get_web_zotero_client


def get_annotations(
    item_key: str | None = None,
    use_pdf_extraction: bool = False,
    limit: int | str | None = None,
    *,
    ctx: Context
) -> str:
    """
    Get annotations from your Zotero library.

    Args:
        item_key: Optional Zotero item key/ID to filter annotations by parent item
        use_pdf_extraction: Whether to attempt direct PDF extraction as a fallback
        limit: Maximum number of annotations to return
        ctx: MCP context

    Returns:
        Markdown-formatted list of annotations
    """
    return _get_annotations(
        item_key=item_key,
        use_pdf_extraction=use_pdf_extraction,
        limit=limit,
        ctx=ctx
    )


def _get_annotations(
    item_key: str | None = None,
    use_pdf_extraction: bool = False,
    limit: int | str | None = None,
    *,
    ctx: Context
) -> str:
    try:
        # Initialize Zotero client
        zot = get_zotero_client()

        # Prepare annotations list
        annotations = []
        parent_title = "Untitled Item"

        # If an item key is provided, use specialized retrieval
        if item_key:
            # First, verify the item exists and get its details
            try:
                parent = zot.item(item_key)
                parent_title = parent["data"].get("title", "Untitled Item")
                ctx.info(f"Fetching annotations for item: {parent_title}")
            except Exception:
                return f"Error: No item found with key: {item_key}"

            # Initialize annotation sources
            better_bibtex_annotations = []
            zotero_api_annotations = []
            pdf_annotations = []

            # Try Better BibTeX method (local Zotero only)
            if os.environ.get("ZOTERO_LOCAL", "").lower() in ["true", "yes", "1"]:
                try:
                    # Import Better BibTeX dependencies
                    from zotero_mcp.better_bibtex_client import (
                        ZoteroBetterBibTexAPI,
                        process_annotation,
                        get_color_category
                    )

                    # Initialize Better BibTeX client
                    bibtex = ZoteroBetterBibTexAPI()

                    # Check if Zotero with Better BibTeX is running
                    if bibtex.is_zotero_running():
                        # Extract citation key
                        citation_key = None

                        # Try to find citation key in Extra field
                        try:
                            extra_field = parent["data"].get("extra", "")
                            for line in extra_field.split("\n"):
                                if line.lower().startswith("citation key:"):
                                    citation_key = line.replace("Citation Key:", "").strip()
                                    break
                                elif line.lower().startswith("citationkey:"):
                                    citation_key = line.replace("citationkey:", "").strip()
                                    break
                        except Exception as e:
                            ctx.warn(f"Error extracting citation key from Extra field: {e}")

                        # Fallback to searching by title if no citation key found
                        if not citation_key:
                            title = parent["data"].get("title", "")
                            try:
                                if title:
                                    # Use the search_citekeys method
                                    search_results = bibtex.search_citekeys(title)

                                    # Find the matching item
                                    for result in search_results:
                                        ctx.info(f"Checking result: {result}")

                                        # Try to match with item key if possible
                                        if result.get('citekey'):
                                            citation_key = result['citekey']
                                            break
                            except Exception as e:
                                ctx.warn(f"Error searching for citation key: {e}")

                        # Process annotations if citation key found
                        if citation_key:
                            try:
                                # Determine library
                                library = "*"  # Default all libraries
                                search_results = bibtex._make_request("item.search", [citation_key])
                                if search_results:
                                    matched_item = next((item for item in search_results if item.get('citekey') == citation_key), None)
                                    if matched_item:
                                        library = matched_item.get('library', "*")

                                # Get attachments
                                attachments = bibtex.get_attachments(citation_key, library)

                                # Process annotations from attachments
                                for attachment in attachments:
                                    annotations_list = bibtex.get_annotations_from_attachment(attachment)

                                    for anno in annotations_list:
                                        processed = process_annotation(anno, attachment)
                                        if processed:
                                            # Create Zotero-like annotation object
                                            bibtex_anno = {
                                                "key": processed.get("id", ""),
                                                "data": {
                                                    "itemType": "annotation",
                                                    "annotationType": processed.get("type", "highlight"),
                                                    "annotationText": processed.get("annotatedText", ""),
                                                    "annotationComment": processed.get("comment", ""),
                                                    "annotationColor": processed.get("color", ""),
                                                    "parentItem": item_key,
                                                    "tags": [],
                                                    "_pdf_page": processed.get("page", 0),
                                                    "_pageLabel": processed.get("pageLabel", ""),
                                                    "_attachment_title": attachment.get("title", ""),
                                                    "_color_category": get_color_category(processed.get("color", "")),
                                                    "_from_better_bibtex": True
                                                }
                                            }
                                            better_bibtex_annotations.append(bibtex_anno)

                                ctx.info(f"Retrieved {len(better_bibtex_annotations)} annotations via Better BibTeX")
                            except Exception as e:
                                ctx.warn(f"Error processing Better BibTeX annotations: {e}")
                except Exception as bibtex_error:
                    ctx.warn(f"Error initializing Better BibTeX: {bibtex_error}")

            # Fallback to Zotero API annotations
            if not better_bibtex_annotations:
                try:
                    # Get child annotations via Zotero API
                    children = zot.children(item_key)
                    zotero_api_annotations = [
                        item for item in children
                        if item.get("data", {}).get("itemType") == "annotation"
                    ]
                    ctx.info(f"Retrieved {len(zotero_api_annotations)} annotations via Zotero API")
                except Exception as api_error:
                    ctx.warn(f"Error retrieving Zotero API annotations: {api_error}")

            # PDF Extraction fallback
            if use_pdf_extraction and not (better_bibtex_annotations or zotero_api_annotations):
                try:
                    from zotero_mcp.pdfannots_helper import extract_annotations_from_pdf, ensure_pdfannots_installed

                    # Ensure PDF annotation tool is installed
                    if ensure_pdfannots_installed():
                        # Get PDF attachments
                        children = zot.children(item_key)
                        pdf_attachments = [
                            item for item in children
                            if item.get("data", {}).get("contentType") == "application/pdf"
                        ]

                        # Extract annotations from PDFs
                        for attachment in pdf_attachments:
                            with tempfile.TemporaryDirectory() as tmpdir:
                                att_key = attachment.get("key", "")
                                file_path = os.path.join(tmpdir, f"{att_key}.pdf")
                                zot.dump(
                                    att_key,
                                    filename=os.path.basename(file_path),
                                    path=tmpdir,
                                )

                                if os.path.exists(file_path):
                                    extracted = extract_annotations_from_pdf(file_path, tmpdir)

                                    for ext in extracted:
                                        # Skip empty annotations
                                        if not ext.get("annotatedText") and not ext.get("comment"):
                                            continue

                                        # Create Zotero-like annotation object
                                        pdf_anno = {
                                            "key": f"pdf_{att_key}_{ext.get('id', uuid.uuid4().hex[:8])}",
                                            "data": {
                                                "itemType": "annotation",
                                                "annotationType": ext.get("type", "highlight"),
                                                "annotationText": ext.get("annotatedText", ""),
                                                "annotationComment": ext.get("comment", ""),
                                                "annotationColor": ext.get("color", ""),
                                                "parentItem": item_key,
                                                "tags": [],
                                                "_pdf_page": ext.get("page", 0),
                                                "_from_pdf_extraction": True,
                                                "_attachment_title": attachment.get("data", {}).get("title", "PDF")
                                            }
                                        }

                                        # Handle image annotations
                                        if ext.get("type") == "image" and ext.get("imageRelativePath"):
                                            pdf_anno["data"]["_image_path"] = os.path.join(tmpdir, ext.get("imageRelativePath"))

                                        pdf_annotations.append(pdf_anno)

                        ctx.info(f"Retrieved {len(pdf_annotations)} annotations via PDF extraction")
                except Exception as pdf_error:
                    ctx.warn(f"Error during PDF annotation extraction: {pdf_error}")

            # Combine annotations from all sources
            annotations = better_bibtex_annotations + zotero_api_annotations + pdf_annotations

        else:
            # Retrieve all annotations in the library
            if isinstance(limit, str):
                limit = int(limit)
            zot.add_parameters(itemType="annotation", limit=limit or 50)
            annotations = zot.everything(zot.items())

        # Handle no annotations found
        if not annotations:
            return f"No annotations found{f' for item: {parent_title}' if item_key else ''}."

        # Generate markdown output
        output = [f"# Annotations{f' for: {parent_title}' if item_key else ''}", ""]

        for i, anno in enumerate(annotations, 1):
            data = anno.get("data", {})

            # Annotation details
            anno_type = data.get("annotationType", "Unknown type")
            anno_text = data.get("annotationText", "")
            anno_comment = data.get("annotationComment", "")
            anno_color = data.get("annotationColor", "")
            anno_key = anno.get("key", "")

            # Parent item context for library-wide retrieval
            parent_info = ""
            if not item_key and (parent_key := data.get("parentItem")):
                try:
                    parent = zot.item(parent_key)
                    parent_title = parent["data"].get("title", "Untitled")
                    parent_info = f" (from \"{parent_title}\")"
                except Exception:
                    parent_info = f" (parent key: {parent_key})"

            # Annotation source details
            source_info = ""
            if data.get("_from_better_bibtex", False):
                source_info = " (extracted via Better BibTeX)"
            elif data.get("_from_pdf_extraction", False):
                source_info = " (extracted directly from PDF)"

            # Attachment context
            attachment_info = ""
            if "_attachment_title" in data and data["_attachment_title"]:
                attachment_info = f" in {data['_attachment_title']}"

            # Build markdown annotation entry
            output.append(f"## Annotation {i}{parent_info}{attachment_info}{source_info}")
            output.append(f"**Type:** {anno_type}")
            output.append(f"**Key:** {anno_key}")

            # Color information
            if anno_color:
                output.append(f"**Color:** {anno_color}")
                if "_color_category" in data and data["_color_category"]:
                    output.append(f"**Color Category:** {data['_color_category']}")

            # Page information
            if "_pdf_page" in data:
                label = data.get("_pageLabel", str(data["_pdf_page"]))
                output.append(f"**Page:** {data['_pdf_page']} (Label: {label})")

            # Annotation content
            if anno_text:
                output.append(f"**Text:** {anno_text}")

            if anno_comment:
                output.append(f"**Comment:** {anno_comment}")

            # Image annotation
            if "_image_path" in data and os.path.exists(data["_image_path"]):
                output.append("**Image:** This annotation includes an image (not displayed in this interface)")

            # Tags
            if tags := data.get("tags"):
                tag_list = [f"`{tag['tag']}`" for tag in tags]
                if tag_list:
                    output.append(f"**Tags:** {' '.join(tag_list)}")

            output.append("")  # Empty line between annotations

        return "\n".join(output)

    except Exception as e:
        ctx.error(f"Error fetching annotations: {str(e)}")
        return f"Error fetching annotations: {str(e)}"


def create_annotation(
    attachment_key: str,
    page: int,
    text: str,
    comment: str | None = None,
    color: str = "#ffd400",
    *,
    ctx: Context
) -> str:
    """
    Create a highlight annotation on a PDF or EPUB attachment.

    This tool handles multiple storage configurations:
    - Zotero Cloud Storage: Downloads file via Web API
    - WebDAV Storage: Downloads file via local Zotero (requires Zotero desktop running)
    - Annotations are always created via the Web API (required for write operations)

    Args:
        attachment_key: Attachment key (e.g., "NHZFE5A7")
        page: For PDF: 1-indexed page number. For EPUB: 1-indexed chapter number.
        text: Exact text to highlight (used to find coordinates/CFI)
        comment: Optional comment on the annotation
        color: Highlight color in hex format (default: "#ffd400" yellow)
        ctx: MCP context

    Returns:
        Confirmation message with the new annotation key
    """
    from zotero_mcp.pdf_utils import (
        find_text_position,
        get_page_label,
        build_annotation_position,
        verify_pdf_attachment,
    )

    try:
        ctx.info(f"Creating annotation on attachment {attachment_key}, page {page}")

        # Get clients for different operations
        local_client = get_local_zotero_client()
        web_client = get_web_zotero_client()

        # REQUIREMENT: Web API is required for creating annotations
        # Zotero's local API (port 23119) is read-only
        if not web_client:
            return (
                "Error: Web API credentials required for creating annotations.\n\n"
                "Please configure the following environment variables:\n"
                "- ZOTERO_API_KEY: Your Zotero API key (from zotero.org/settings/keys)\n"
                "- ZOTERO_LIBRARY_ID: Your library ID\n"
                "- ZOTERO_LIBRARY_TYPE: 'user' or 'group'\n\n"
                "Note: Zotero's local API is read-only and cannot create annotations."
            )

        # Use web client for metadata (it has the credentials)
        metadata_client = web_client

        # Verify the attachment exists and is a PDF
        try:
            attachment = metadata_client.item(attachment_key)
            attachment_data = attachment.get("data", {})

            if attachment_data.get("itemType") != "attachment":
                return f"Error: Item {attachment_key} is not an attachment"

            content_type = attachment_data.get("contentType", "")
            supported_types = {
                "application/pdf": "pdf",
                "application/epub+zip": "epub",
            }
            if content_type not in supported_types:
                return f"Error: Attachment {attachment_key} is not a PDF or EPUB (type: {content_type})"

            file_type = supported_types[content_type]
            filename = attachment_data.get("filename", f"{attachment_key}.{file_type}")

        except Exception as e:
            return f"Error: No attachment found with key: {attachment_key} ({e})"

        # Download the PDF to a temporary location
        # Strategy: Try multiple sources in order of likelihood to succeed
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, filename)
            ctx.info(f"Downloading PDF to {file_path}")

            download_errors = []
            downloaded = False

            # Source 1: Try local Zotero first (works for WebDAV and local storage)
            if local_client and not downloaded:
                try:
                    ctx.info("Trying local Zotero (WebDAV/local storage)...")
                    local_client.dump(attachment_key, filename=filename, path=tmpdir)
                    if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                        downloaded = True
                        ctx.info("PDF downloaded via local Zotero")
                except Exception as e:
                    download_errors.append(f"Local Zotero: {e}")

            # Source 2: Try Web API (works for Zotero Cloud Storage)
            if not downloaded:
                try:
                    ctx.info("Trying Zotero Web API (cloud storage)...")
                    web_client.dump(attachment_key, filename=filename, path=tmpdir)
                    if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                        downloaded = True
                        ctx.info("PDF downloaded via Web API")
                except Exception as e:
                    download_errors.append(f"Web API: {e}")

            if not downloaded:
                error_details = "\n".join(f"  - {err}" for err in download_errors)
                return (
                    f"Error: Could not download PDF attachment.\n\n"
                    f"Attempted sources:\n{error_details}\n\n"
                    "Possible solutions:\n"
                    "- **Zotero Cloud Storage**: Ensure file syncing is enabled in Zotero preferences\n"
                    "- **WebDAV Storage**: Ensure Zotero desktop is running with "
                    "'Allow other applications to communicate with Zotero' enabled\n"
                    "- **Linked files**: Linked attachments (not imported) cannot be accessed remotely"
                )

            # Verify the file is valid
            if file_type == "pdf":
                if not verify_pdf_attachment(file_path):
                    return f"Error: Downloaded file is not a valid PDF"
            else:  # epub
                from zotero_mcp.epub_utils import verify_epub_attachment
                if not verify_epub_attachment(file_path):
                    return f"Error: Downloaded file is not a valid EPUB"

            # Search for the text and get position data
            search_preview = text[:50] + "..." if len(text) > 50 else text
            location_type = "page" if file_type == "pdf" else "chapter"
            ctx.info(f"Searching for text in {location_type} {page}: '{search_preview}'")

            if file_type == "pdf":
                position_data = find_text_position(file_path, page, text)
            else:  # epub
                from zotero_mcp.epub_utils import find_text_in_epub
                position_data = find_text_in_epub(file_path, page, text)

            if "error" in position_data:
                # Build debug info message
                debug_lines = [
                    f"Error: {position_data['error']}",
                    f"",
                    f"Text searched: \"{text[:100]}{'...' if len(text) > 100 else ''}\"",
                ]

                best_score = position_data.get("best_score", 0)
                best_match = position_data.get("best_match")

                # Add "Did you mean" suggestion if we found a reasonable match
                if best_score >= 0.5 and best_match:
                    debug_lines.append("")
                    debug_lines.append("=" * 50)
                    debug_lines.append(f"DID YOU MEAN (score: {best_score:.0%}):")
                    debug_lines.append("")
                    # Show a useful preview - first 150 chars of the match
                    suggestion = best_match[:150].strip()
                    if len(best_match) > 150:
                        suggestion += "..."
                    debug_lines.append(f'  "{suggestion}"')
                    debug_lines.append("")
                    if position_data.get("page_found"):
                        debug_lines.append(f"  (Found on page {position_data['page_found']})")
                    debug_lines.append("=" * 50)
                    debug_lines.append("")
                    debug_lines.append("TIP: Copy the exact text from the PDF instead of paraphrasing.")
                elif best_score > 0:
                    debug_lines.append(f"")
                    debug_lines.append(f"Debug info:")
                    debug_lines.append(f"  Best match score: {best_score:.2f} (too low for suggestion)")
                    if best_match:
                        preview = best_match[:80]
                        debug_lines.append(f"  Best match text: \"{preview}...\"")
                    # Handle both PDF (page_found) and EPUB (chapter_found)
                    found_location = position_data.get("page_found") or position_data.get("chapter_found")
                    if found_location:
                        debug_lines.append(f"  Found in {location_type}: {found_location}")

                # Handle both PDF (pages_searched) and EPUB (chapters_searched)
                searched = position_data.get("pages_searched") or position_data.get("chapters_searched")
                if searched:
                    debug_lines.append(f"  {location_type.title()}s searched: {searched}")

                if best_score < 0.5:
                    debug_lines.extend([
                        "",
                        "Tips:",
                        f"- Copy the exact text from the {file_type.upper()} (don't paraphrase)",
                        "- Try a shorter, unique phrase from the beginning",
                        f"- Check that the {location_type} number is correct",
                    ])

                return "\n".join(debug_lines)

            # Build annotation data based on file type
            if file_type == "pdf":
                # Get page label (might differ from page number in some PDFs)
                page_label = get_page_label(file_path, page)

                # Build annotation position JSON for PDF
                annotation_position = build_annotation_position(
                    position_data["pageIndex"],
                    position_data["rects"]
                )
                sort_index = position_data["sort_index"]
            else:  # epub
                # For EPUB: leave pageLabel EMPTY for proper navigation
                # Zotero's manual EPUB annotations have empty pageLabel and it works
                page_label = ""  # Empty, not chapter number!
                annotation_position = position_data["annotation_position"]
                # EPUB sort index format: "spine_index|character_offset"
                # Use actual character position from CFI generation
                chapter = position_data.get("chapter_found", page)
                char_position = position_data.get("char_position", chapter * 1000)
                sort_index = f"{chapter:05d}|{char_position:08d}"

            # Prepare the annotation data
            annotation_data = {
                "itemType": "annotation",
                "parentItem": attachment_key,
                "annotationType": "highlight",
                "annotationText": text,
                "annotationComment": comment or "",
                "annotationColor": color,
                "annotationSortIndex": sort_index,
                "annotationPosition": annotation_position,
            }
            # Only add pageLabel if not empty (EPUB should not have it)
            if page_label:
                annotation_data["annotationPageLabel"] = page_label

            ctx.info(f"Creating annotation via Web API...")

            # Create the annotation using web client
            result = web_client.create_items([annotation_data])

            # Check if creation was successful
            if "success" in result and result["success"]:
                successful = result["success"]
                if len(successful) > 0:
                    annotation_key = list(successful.values())[0]
                    location_label = "Page" if file_type == "pdf" else "Chapter"
                    response = [
                        f"Successfully created highlight annotation",
                        f"",
                        f"**Annotation Key:** {annotation_key}",
                        f"**{location_label}:** {page_label}",
                    ]
                    # For EPUB, show if text was found in different chapter than requested
                    if file_type == "epub":
                        chapter_found = position_data.get("chapter_found", page)
                        if chapter_found != page:
                            response.append(f"**Note:** Text was found in chapter {chapter_found} (you specified {page})")
                        chapter_href = position_data.get("chapter_href", "")
                        if chapter_href:
                            response.append(f"**Section:** {chapter_href}")
                    response.append(f"**Text:** \"{text[:100]}{'...' if len(text) > 100 else ''}\"")
                    if comment:
                        response.append(f"**Comment:** {comment}")
                    response.append(f"**Color:** {color}")
                    return "\n".join(response)
                else:
                    return f"Annotation creation response was successful but no key was returned: {result}"
            else:
                failed_info = result.get("failed", {})
                return f"Failed to create annotation: {failed_info}"

    except Exception as e:
        ctx.error(f"Error creating annotation: {str(e)}")
        return f"Error creating annotation: {str(e)}"


def register(mcp: FastMCP) -> None:
    """Register annotation tools."""
    mcp.tool(
        name="zotero_get_annotations",
        description="Get all annotations for a specific item or across your entire Zotero library."
    )(get_annotations)

    mcp.tool(
        name="zotero_create_annotation",
        description="Create a highlight annotation on a PDF or EPUB attachment with optional comment."
    )(create_annotation)

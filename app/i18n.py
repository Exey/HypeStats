"""Dictionary-based i18n (EN / RU, switchable at runtime)."""
from __future__ import annotations

EN = {
    "app_title": "HypeStats",
    # nav / sidebar
    "nav_config": "⚙️ Config",
    "nav_no_channels": "No channels yet — fetch one from the Config screen.",
    "nav_compare": "Metrics",
    "nav_compare_hint": "Pick 2-8 channels to compare.",
    "nav_sort_folders": "Sort by Members",
    "nav_sort_folders_active": "Sort by Folders",
    "nav_sort_folders_hint": "Group the channel list by folder (folder "
                            "order, unassigned last), sorted by followers "
                            "within each group — instead of one flat list "
                            "sorted by followers.",
    "nav_fold_hint": "Collapse sidebar",
    "nav_unfold_hint": "Expand sidebar",
    "nav_lang_hint": "Switch language",
    "nav_compare_md_hint": "Export the current comparison as a Markdown table",
    "compare_title": "Compare channels",
    "compare_md_metric": "Metric",
    "compare_content_quality": "CONTENT QUALITY",
    # folders (right-click a channel in the sidebar, or the dashboard's folder
    # button / Config screen's Folders card)
    "folder_none": "No folder",
    "folder_manage": "Manage folders…",
    "folder_manage_title": "Manage folders",
    "folder_add": "+ Add folder",
    "folder_name_prompt": "Folder name:",
    "folder_close": "Close",
    "folder_pick_color": "Pick a color",
    "folder_change_color": "Change color",
    "folder_delete": "Delete folder",
    "folder_delete_confirm": "Delete folder “{name}”? Channels in it keep their "
                             "data — only the folder assignment is removed.",
    "folder_section_title": "Folders",
    "folder_manage_help": "Create, rename, recolor or delete the folders used "
                          "to organize channels in the sidebar.",
    "folder_list_empty": "No folders yet.",
    "folder_choose": "Assign this channel to a folder",
    "folder_export_md_btn": "Export to MD",
    "folder_export_md_hint": "Save every tracked channel as a Markdown "
                             "table, grouped by folder (folder order, "
                             "unassigned last) and sorted by followers "
                             "within each group.",
    "folder_export_col_folder": "Folder",
    "folder_export_col_followers": "Followers",
    "folder_export_col_id": "ID/Username",
    "folder_export_extra_cols": "Rating, views, viral share, post quality",
    "folder_export_extra_cols_hint": "Add Rating (composite score, same "
                                     "formula as Folder Stats), Views "
                                     "(total for the period), Viral share "
                                     "and Post Quality (average gauge "
                                     "score) columns — reads every "
                                     "channel's full checkpoint, not just "
                                     "the sidebar summary.",
    "folder_export_period_hint": "Which period Rating/Views/Viral share are "
                                 "computed over — the channel's most recent "
                                 "half-year or season, or its whole "
                                 "all-time stats.",
    "folder_export_ethics_chk": "Calculate Ethics",
    "folder_export_ethics_hint": "Before exporting, (re)calculate each "
                                 "in-scope channel's Ethics score (see the "
                                 "Mentions view's own \"Ethics\" card) "
                                 "and cache it to its checkpoint — runs a "
                                 "background job first, since it scans "
                                 "every scanned post's links. Leave "
                                 "unchecked to just export whatever's "
                                 "already cached (blank for a channel "
                                 "that's never had it calculated).",
    "folder_export_ethics_folder_hint": "Which channels to (re)calculate "
                                        "Ethics for.",
    "folder_export_ethics_all": "All channels",
    "folder_export_ethics_tags_chk": "Only with Tags",
    "folder_export_ethics_tags_hint": "Further narrow the scope above to "
                                      "just channels that already have a "
                                      "tag assigned (any tag).",
    "folder_export_col_ethics": "Ethics",
    "folder_export_rating_trend_note": "_Rating is docked for a channel whose "
                                       "year-over-year activity is slowing "
                                       "(−20%), stalling (−33%) or abandoned "
                                       "(−50%)._",
    "folder_export_alltime_note": "_Ethics is computed over each channel's "
                                  "whole parsed history; the period above only "
                                  "scopes Rating / Views / Viral share / Post "
                                  "Quality._",
    "folder_mentions_refresh_label": "Refresh mentions for:",
    "folder_mentions_refresh_btn": "🔗 Refresh mentions",
    "folder_mentions_refresh_hint": "Re-fetch just the links in every "
                                    "already-textual stored post in this "
                                    "folder's channels — much faster than a "
                                    "full re-fetch since it skips everything "
                                    "else.",
    "folder_comments_refresh_label": "Refresh comments for:",
    "folder_comments_refresh_btn": "💬 Refresh comments",
    "folder_comments_refresh_hint": "Re-fetch just the comment count for "
                                    "every stored post in this folder's "
                                    "channels — much faster than a full "
                                    "re-fetch since it skips everything "
                                    "else.",
    "folder_assign_all_label": "Assign every channel to:",
    "folder_assign_all_btn": "Assign all",
    "folder_assign_all_hint": "Move every tracked channel into this folder, "
                              "replacing any folder it's currently in.",
    "folder_assign_all_confirm": "Move all {count} channels into "
                                 "“{folder}”? This replaces any "
                                 "folder they're currently assigned to.",
    "folder_assign_all_none": "No channels to assign yet — fetch one from "
                              "this screen first.",
    "folder_export_col_tag": "Tag",
    # lean refresh (Config screen's staleness list + batch re-fetch)
    "lean_refresh_title": "Lean refresh",
    "lean_refresh_help": "Bring stale checkpoints up to date without a full "
                         "re-scan — a lean refresh only re-reads the months "
                         "since each channel was last fetched and merges them "
                         "in. Progress and log show in the Fetch panel above.",
    "lean_refresh_oldest_btn": "Oldest 10",
    "lean_refresh_oldest_hint": "Lean-refresh the 10 least-recently-updated "
                                "channels.",
    "lean_refresh_1mo_btn": "1 mo+",
    "lean_refresh_1mo_hint": "Lean-refresh every channel last updated more "
                             "than a month ago.",
    "lean_refresh_3mo_btn": "3 mo+",
    "lean_refresh_3mo_hint": "Lean-refresh every channel last updated more "
                             "than 3 months ago.",
    "lean_refresh_selected_btn": "Refresh selected",
    "lean_refresh_selected_hint": "Lean-refresh only the channels ticked in "
                                  "the list below.",
    "lean_refresh_selected_2y_btn": "Re-fetch selected · 2 y",
    "lean_refresh_selected_2y_hint": "Full re-scan (not incremental) of the "
                                     "ticked channels over the last 2 years — "
                                     "rebuilds every month with the current "
                                     "per-post fields (reposts, buttons, "
                                     "comments). Slower than a lean refresh.",
    "lean_refresh_selected_all_btn": "Re-fetch selected · All time",
    "lean_refresh_selected_all_hint": "Full re-scan (not incremental) of the "
                                      "ticked channels over their entire "
                                      "history — rebuilds every month with "
                                      "the current per-post fields (reposts, "
                                      "buttons, comments). Much slower than "
                                      "a 2-year re-fetch on a long-running "
                                      "channel.",
    "lean_refetch_mentions_btn": "🔗 Refetch mentions",
    "lean_refetch_mentions_hint": "Re-fetch just the links (see "
                                  "tools.mentions_refresh) for the ticked "
                                  "channels' already-textual stored posts — "
                                  "same job as the folder-scoped \"Refresh "
                                  "mentions for:\" row above, targeting the "
                                  "ticked channels instead of a whole folder.",
    "mentions_refresh_confirm": "Refetch mention links for {count} channel(s)?",
    "lean_refresh_confirm": "Lean-refresh {count} channel(s)?",
    "lean_refresh_full_confirm": "Full re-fetch of {count} channel(s) over the "
                                 "last 2 years? Slower than a lean refresh.",
    "lean_refresh_all_confirm": "Full re-fetch of {count} channel(s) over "
                                "their ENTIRE history? This can take a long "
                                "time and use significant API quota on a "
                                "channel with years of posts.",
    "lean_refresh_none": "Nothing that stale — every channel is more recent "
                         "than that.",
    "lean_refresh_none_selected": "Tick at least one channel in the list first.",
    "lean_refresh_empty": "No channels tracked yet.",
    "lean_refresh_col_updated": "Updated",
    "lean_refresh_col_channel": "Channel",
    # Mentions export card (Config screen, mounted on the Folders & Tags
    # view below the Folders/Tags cards) — batch Link report/Mentions
    # report export for a whole folder, see tools.mentions_export.
    "mentions_export_title": "Mentions",
    "mentions_export_help": "Export the Mentions view's Link report or "
                            "Mentions report for every channel in a folder "
                            "at once — one Markdown file, sections "
                            "separated by channel title.",
    "mentions_export_folder_label": "Folder:",
    "mentions_export_link_btn": "All Link Reports",
    "mentions_export_link_hint": "Every channel's Link report (fair/fake/"
                                 "unresolved/promo links, most-repeated "
                                 "first) in the selected folder, combined "
                                 "into one Markdown file.",
    "mentions_export_mentions_btn": "All Mentions Report",
    "mentions_export_mentions_hint": "Every channel's Mentions report "
                                     "(every collected name, linked or "
                                     "not) in the selected folder, "
                                     "combined into one Markdown file.",
    # tags (Config screen's Tags card, sidebar badges, dashboard's tag button)
    "tag_section_title": "Tags",
    "tag_manage_help": "Load a Markdown table (| tag | long tag | description "
                       "|) to define the available tags. Editing tags.md and "
                       "loading it again replaces the whole list — assign a "
                       "tag to a channel from the sidebar's right-click menu "
                       "or the dashboard's tag button.",
    "tag_list_empty": "No tags loaded yet.",
    "tag_load_md_btn": "Load MD",
    "tag_load_md_hint": "Pick a Markdown file shaped like "
                        "\"| tag | long tag | description |\" — replaces "
                        "the current tag list; channels assigned to a "
                        "removed tag become untagged.",
    "tag_load_md_done": "Loaded {n} tags.",
    "tag_load_md_empty": "No tags found in that file — check it matches "
                         "the \"| tag | long tag | description |\" format.",
    "tag_none": "No tag",
    "tag_choose": "Assign this channel to a tag",
    # folder stat view
    "nav_folder_stat": "📁 Folders & Tags",
    "folder_stat_sub": "Manage folders and tags, plus per-period stats and a "
                       "composite Rating for one folder",
    "folder_stat_pick_folder": "Folder:",
    "folder_stat_no_folders": "No folders yet — create one from the Config screen.",
    "folder_stat_empty_channels": "This folder has no channels yet.",
    "folder_stat_links_title": "Channel links",
    "folder_stat_links_hint": "Reposts between this folder's channels, detected "
                              "from each channel's top posts. Needs \"Include "
                              "public reposts\" enabled when a channel was fetched.",
    "folder_stat_links_empty": "No cross-channel reposts found.",
    "folder_stat_period_title": "Periodic stats",
    "folder_stat_period_hint": "Per-period views/shares/reactions/viral share "
                               "and the featured post are computed from each "
                               "channel's full post history (needs a refetch "
                               "on older checkpoints). Name and Website are "
                               "left blank — fill them in later.",
    "period_mode_month": "Monthly",
    "period_mode_season": "Seasonal",
    "period_mode_halfyear": "Half-Year",
    "period_mode_year": "Year",
    "period_year_half": "Half-year",
    "period_year_1y": "Last Year",
    "period_year_1_5y": "1.5 Year",
    "period_year_2y": "2 Year",
    "period_year_all": "All Fetched Time",
    "col_channel_title": "Title",
    "col_username_id": "Username/ID",
    "col_shares": "Shares",
    "col_most_viewed": "Most viewed post",
    "col_viral_share": "Viral share",
    "col_rating": "Rating",
    "col_post_quality": "Post Quality",
    "folder_stat_period_empty": "No posts found for this folder.",
    # mutual pr view
    "nav_mutual_pr": "🤝 Mutual PR",
    "mutual_pr_sub": "Compare tracked channels for ad-swaps: reach, quality, "
                     "and an estimated follower-gain forecast per ad post.",
    "mutual_pr_hint": "The forecast and best-days columns are estimates, not "
                      "measurements — this app has no real ad-campaign outcome "
                      "data. They factor in each channel's recent content "
                      "quality, itself only as complete as each checkpoint's "
                      "stored post sample for the last ~3 months, plus a fixed "
                      "view-accumulation curve and reach-to-follower conversion "
                      "rate (see app.scoring_pr). Treat these as a starting "
                      "point for a conversation, not a guarantee.",
    "mutual_pr_pick_folder": "Folder:",
    "mutual_pr_all_channels": "All channels",
    "mutual_pr_empty": "No channels tracked yet — fetch one from the Config screen.",
    "mutual_pr_range_tooltip": "Rough range: {low}–{high} (crude uncertainty band, "
                               "not a fitted interval — see app.scoring_pr)",
    "col_followers": "Followers",
    "col_forecast_24h": "24h forecast",
    "col_repeated_after_month": "Repeat in Month",
    "col_forecast_48h": "48h forecast",
    "col_forecast_72h": "72h forecast",
    "col_forecast_week": "Week forecast",
    "col_forecast_month": "Month forecast",
    "col_best_days": "Best days to post",
    "activity_verdict_active": "active",
    "activity_verdict_slowing": "slowing",
    "activity_verdict_stalling": "stalling",
    "activity_verdict_abandoned": "abandoned",
    "mutual_pr_activity_tooltip": "Activity: {verdict} (forecast reduced {pct}%)",
    # mutual pr — cross-channel reposts table (moved here from Folder Stats)
    "mutual_pr_links_title": "Channel links",
    "mutual_pr_links_hint": "Which tracked channels already link to which "
                            "others, from every t.me link found in a post's "
                            "caption — not just Telegram's own \"public "
                            "forward\" stat, so this works even without "
                            "\"Include public reposts\" enabled. Cached: press "
                            "Calculate/Recalculate to (re)scan the whole "
                            "tracked base.",
    "mutual_pr_links_empty": "No cross-channel links found — press Calculate, "
                             "or widen the folder filter above.",
    "mutual_pr_links_calc_btn": "Calculate",
    "mutual_pr_links_recalc_btn": "Recalculate",
    "mutual_pr_links_calculating": "Calculating…",
    "mutual_pr_links_calculated_at": "Calculated {when}",
    "mutual_pr_links_col_channel": "Channel",
    "mutual_pr_links_col_by": "Mentioned by",
    "mutual_pr_links_col_count": "Mentions",
    "mutual_pr_links_col_top_source": "Top source",
    "mutual_pr_links_also": "Also mentioned by: {names}",
    # mutual pr — MPR Pairs (card + Markdown export)
    "mutual_pr_partners_title": "MPR Pairs",
    "mutual_pr_partners_hint": "\"Best posting days\" shows each channel's own "
                               "best days (A / B); ★ marks the days that are a "
                               "good ad slot in both channels at once. Excludes "
                               "any pair the Channel links card already shows "
                               "as connected (see above), and keeps at most 16 "
                               "of any one channel's own best pairs, so one "
                               "generalist channel can't crowd out the rest.",
    "mutual_pr_partners_empty": "No channel pairs to rank yet.",
    "mutual_pr_partners_col_a": "Channel A",
    "mutual_pr_partners_col_b": "Channel B",
    "mutual_pr_partners_col_days": "Best posting days",
    "mutual_pr_partners_col_forecast": "24h forecast (A / B)",
    # high-quality posts view
    "nav_content_quality": "🎯 High-Quality Posts",
    "cqi_fetch_media": "🖼 Fetch media",
    "cqi_fetch_media_running": "Fetching…",
    "cqi_fetch_media_hint": "Download a small preview image for each post "
                            "shown below (only posts without one already "
                            "cached are fetched)",
    "cqi_fetch_media_need_login": "Add your Telegram API credentials on the "
                                  "Config screen first.",
    "cqi_fetch_media_all_cached": "Every post shown below already has a "
                                  "cached thumbnail — nothing new to fetch.",
    "cqi_fetch_media_login_required": "This needs a Telegram login — please "
                                      "log in from the Config screen first, "
                                      "then try again.",
    "cqi_empty_posts": "No posts found for this folder in this period.",
    "cqi_post_tooltip": "{label}\nScore: {score}",
    "cqi_post_tooltip_formula": "reaction weight = min(reactions,{t1cap})×{t1w} "
                                "+ max(0, min(reactions,{t2cap})−{t1cap})×{t2w}\n"
                                "               = min({reactions},{t1cap})×{t1w} "
                                "+ max(0, min({reactions},{t2cap})−{t1cap})×{t2w}\n"
                                "               = {reaction_weighted}\n\n"
                                "viral excess = max(0, views − channel avg) "
                                "= max(0, {views} − {avg_views}) = {viral_excess}\n\n"
                                "ERV% = (forwards×{fwd_w} + comments(≤100)×{cmt_w} "
                                "+ reaction weight + viral excess×{vrl_w}) "
                                "/ views × 100\n"
                                "     = ({forwards}×{fwd_w} + {comments}×{cmt_w} "
                                "+ {reaction_weighted} + {viral_excess}×{vrl_w}) "
                                "/ {views} × 100\n"
                                "     = {erv}%\n"
                                "raw score = ERV% × 100 = {raw}\n"
                                "gauge = raw / (raw + {k}) × 1000 = {gauge}",
    "cqi_tg_links": "🅰️ Tg Links",
    "cqi_tg_links_hint": "Generate a copyable text list of links to the "
                         "posts shown below, ranked by score.",
    "cqi_tg_links_dialog_title": "Tg Links",
    "cqi_tg_links_header": "{folder} / {period}",
    "cqi_tg_links_top_authors_title": "Best {n} authors",
    "cqi_export_md_btn": "MD",
    "cqi_export_md_hint": "Save the posts shown below as a Markdown table "
                          "with each post's cached thumbnail embedded "
                          "inline (base64) — self-contained, unlike the "
                          "Tg Links list.",
    "cqi_md_col_channel": "Channel",
    "cqi_md_col_score": "Score CQI",
    "cqi_md_col_thumbnail": "Thumbnail",
    "cqi_md_col_media": "Media",
    "cqi_md_col_text": "Text",
    "cqi_md_col_link": "Link",
    "cqi_md_hitmakers_title": "Top {n} {folder} hitmakers",
    "cqi_all_folders": "All folders",
    "cqi_max_posts_n": "Top {n}",
    "cqi_max_posts_hint": "How many top posts to show overall — in the grid, "
                         "the per-channel limit and the Tg Links list.",
    "cqi_tg_links_limit_hint": "Limit how many posts from the same channel "
                               "are shown below and in the Tg Links list. "
                               "\"Rein in dominant channel\" sets no fixed "
                               "cap but, on a Top 50+ slate, trims any one "
                               "channel holding more than 11 slots and 3× "
                               "the typical channel's share down to 11.",
    "cqi_tg_links_limit_none": "No limit",
    "cqi_tg_links_limit_anomaly": "Rein in dominant channel",
    "cqi_tg_links_limit_n": "{n} per channel",
    "cqi_hide_non_media": "Hide non-media posts",
    "cqi_hide_non_media_hint": "Hide text-only posts (no photo, video, "
                               "voice/audio or other file) from the grid, "
                               "the per-channel limit and the Tg Links list.",
    "cqi_min_followers_none": "Any followers",
    "cqi_min_followers_n": "≥{n} followers",
    "cqi_min_followers_hint": "Only rank posts from channels with at least "
                              "this many followers — a tiny, highly-engaged "
                              "channel can otherwise crowd out posts from "
                              "bigger channels worth featuring.",
    # compare charts view
    "nav_compare_charts": "Charts",
    "nav_compare_charts_hint": "Pick up to 8 channels to compare on charts.",
    "compare_charts_sub": "Views, shares and reactions over time for the "
                          "channels selected in the sidebar",
    # mentions view
    "nav_mentions": "Mentions",
    "nav_mentions_hint": "Pick up to 4 channels to compare post texts and "
                         "the names mentioned in them.",
    "mentions_no_extraction": "Name extraction isn't available (transformers "
                              "isn't installed, or its model couldn't be "
                              "loaded) — texts still show below, but no names "
                              "will be extracted from them.",
    "mentions_period_label": "Mentions Period:",
    "mentions_period_all": "All time",
    "mentions_reload_btn": "Reload",
    "mentions_reload_hint": "Re-read mentions.md from disk",
    "mentions_reload_confirm": "Discard unsaved edits and reload mentions.md "
                               "from disk?",
    "mentions_empty": "Pick up to 4 channels from the sidebar's Mentions mode.",
    "mentions_names_title_counts": "Posts {posts}/{total_posts}, Names Found: "
                                   "{total}, in mentions.md {linked}",
    "mentions_links_title_counts": "Posts {posts}/{total_posts}, Telegram "
                                   "links {tg}, Web links {web}, ",
    "mentions_links_title_most": "Most repeat ({count}): ",
    "mentions_links_title_none": "no links",
    "mentions_stats_classifying": "Classifying links…",
    "mentions_stats_balance_value": "{tg}%/{web}%",
    "mentions_stats_none": "—",
    "mentions_card_fairness": "Ethics",
    "mentions_card_fair": "Fair",
    "mentions_card_fake": "Fake",
    "mentions_card_no_link": "No Link",
    "mentions_card_unique": "Unique",
    "mentions_card_tg_web": "Tg / Web",
    "mentions_stats_report_row": "Link report ({count}) →",
    "mentions_stats_unresolved_row": "Unresolved fair links ({count}) →",
    "mentions_report_row": "Mentions report ({count}) →",
    "mentions_report_title": "Mentions report — {channel}",
    "mentions_report_no_link": "No link",
    "mentions_stats_report_title": "Link report — {channel}",
    "mentions_stats_report_empty": "No classified links in scope.",
    "mentions_stats_report_col_status": "Status",
    "mentions_stats_report_col_count": "Count",
    "mentions_stats_report_col_url": "Link",
    "mentions_stats_report_name_hint": "Open the post this was extracted from",
    "mentions_open_link_btn": "Open",
    "mentions_source_btn": "Source",
    "mentions_stats_report_sources_title": "Source posts",
    "mentions_link_status_fair": "Fair",
    "mentions_link_status_fake": "Fake",
    "mentions_link_status_unresolved": "Unresolved",
    "mentions_link_status_promo": "Promo",
    "mentions_stats_unresolved_title": "Unresolved fair links — {channel}",
    "mentions_stats_unresolved_empty": "Nothing to link — every "
                                       "Telegram-linked name is already in "
                                       "mentions.md.",
    "mentions_stats_unresolved_col_link": "Link",
    "mentions_similar_title": "Similar mentions",
    "mentions_similar_md_btn": "Similar MD",
    "mentions_similar_md_hint": "Export the people two or more loaded channels "
                                "both mention as a Markdown table: name "
                                "variants, then one column per channel with "
                                "links to its posts.",
    "mentions_similar_md_empty": "No names shared by two or more of the loaded "
                                 "channels.",
    "mentions_similar_md_col_names": "Name variants",
    "mentions_table_title": "mentions.md",
    "mentions_locate_btn": "Show in folder",
    "mentions_locate_hint": "Reveal mentions.md in Finder/Explorer",
    "mentions_add_row_btn": "+ Row",
    "mentions_save_btn": "Save",
    "mentions_col_id": "id",
    "mentions_col_names": "names",
    "mentions_col_links": "unclear links",
    "mentions_col_name": "Name",
    "mentions_col_found": "mentions.md",
    "mentions_col_posts": "Posts",
    "mentions_col_post_id": "ID",
    "mentions_col_date": "Date",
    "mentions_col_type": "Type",
    "mentions_col_text": "Text",
    "mentions_forwarded_from": "Forwarded from {name}",
    "mentions_forwarded": "Forwarded",
    "mentions_link_btn": "Link…",
    "mentions_link_tg_hint": "Backed by an actual Telegram link in the post "
                            "— higher confidence than text extraction alone",
    "mentions_mark_btn": "Mark…",
    "mentions_mark_hint": "Name extraction found nothing in this post — "
                          "type the name yourself to link it",
    "mentions_ignore_btn": "Ignore",
    "mentions_ignore_hint": "Not a name — never extract this again",
    "mentions_link_new": "+ New id…",
    "mentions_new_id_prompt": "Channel @username or full name (ФИО) for "
                              "this person:",
    "mentions_confirm_name_title": "Confirm name",
    "mentions_confirm_name_prompt": "Text to attach (edit if the extractor "
                                    "got this wrong):",
    "mentions_delete_row_hint": "Remove this row from mentions.md",
    "mentions_delete_row_confirm": "Remove \"{id}\" from mentions.md?",
    "compare_charts_empty": "Pick up to 8 channels from the sidebar to plot "
                            "them here.",
    # menus
    "menu_file": "File",
    "menu_language": "Language",
    "menu_theme": "Theme",
    "import_env": "Import .env into current profile…",
    "export_env": "Export current profile as .env…",
    "open_config_folder": "Open config folder",
    "quit": "Quit",
    "lang_en": "English",
    "lang_ru": "Русский",
    "theme_system": "System",
    "theme_light": "Light",
    "theme_dark": "Dark",
    "theme_black": "Black (AMOLED)",
    "zoom_label": "Interface size",
    "zoom_small": "Small (−2 pt, titles −3)",
    "zoom_standard": "Standard",
    "zoom_large": "Large (+2 pt, titles +1)",
    "accent_label": "Accent color",
    "accent_default": "Default",
    "accent_custom": "Custom…",
    "env_imported": "Imported {n} value(s) from .env.",
    # generic
    "save": "💾 Save profile",
    "saved": "Profile saved.",
    "stop": "⏹ Stop",
    "done_ok": "✅ Finished: {msg}",
    "done_fail": "❌ Failed: {msg}",
    "cancelled": "⏹ Cancelled by user.",
    "missing_conn": "Fill in API_ID, API_HASH and PHONE_NUMBER first.",
    "worker_running": "A fetch is still running. Stop it first.",
    # profiles
    "profile": "Profile",
    "new_profile": "New…",
    "delete_profile": "Delete",
    "profile_name": "Profile name:",
    "delete_profile_confirm": "Delete profile “{name}”?",
    # connection fields
    "field_API_ID": "API_ID",
    "field_API_HASH": "API_HASH",
    "field_PHONE_NUMBER": "PHONE_NUMBER",
    "config_location": "Config file: {path}",
    "instructions_title": "📖 How to get API_ID and API_HASH",
    "instructions_text": (
        "1. Open <a href='https://my.telegram.org'>my.telegram.org</a> and log in "
        "with your phone number (Telegram sends the code to your Telegram app).<br>"
        "2. Click <b>API development tools</b>.<br>"
        "3. Fill in <i>App title</i> and <i>Short name</i> (any text), "
        "platform <i>Desktop</i>, then create the application.<br>"
        "4. Copy <b>App api_id</b> → API_ID and <b>App api_hash</b> → API_HASH.<br>"
        "5. PHONE_NUMBER must be in international format, e.g. <code>+79001234567</code>.<br><br>"
        "<b>Which channel can I analyze?</b> Any public channel by "
        "<code>@username</code>, or a private one you're a member of by its "
        "<code>-100…</code> ID or t.me link."
    ),
    # login
    "login_code_prompt": "Enter the login code Telegram just sent you:",
    "login_password_prompt": "Enter your two-factor (2FA) password:",
    "login_title": "Telegram login",
    "qr_login_button": "🔳 Login via QR code",
    "qr_login_title": "QR code login",
    "qr_login_hint": "Open Telegram on your phone → Settings → Devices → "
                     "Link Desktop Device, then scan this code.",
    "qr_login_generating": "Generating QR code…",
    "qr_login_waiting": "Waiting for you to scan…",
    "qr_login_expired": "Code expired, generating a new one…",
    "qr_login_success": "Logged in successfully.",
    "check_login_button": "🔎 Check login",
    "check_login_checking": "Checking…",
    "check_login_ok": "✅ Logged in as {name} (+{phone}).",
    "check_login_not_authorized": "❌ Not logged in — use QR login first.",
    # fetch card
    "fetch_title": "Fetch a channel",
    "fetch_help": "Scans a public/private channel over the chosen period: reads "
                  "each post's views, reactions and forwards, ranks the top "
                  "posts, and computes activity stats (members, posts/day, "
                  "hour & weekday patterns). The result is stored as a "
                  "checkpoint and added to the sidebar.",
    "fetch_channel": "Channel ID or @username",
    "fetch_channel_placeholder": "@durov or -1001234567890",
    "fetch_period": "Period of analysis",
    "period_3m": "3 months",
    "period_6m": "6 months",
    "period_1y": "1 year",
    "period_2y": "2 years",
    "period_3y": "3 years",
    "period_all": "All time",
    "fetch_public": "Fetch public reposts (slower, needs stats access)",
    "fetch_button": "📊 Fetch & analyze",
    "fetch_log": "Log",
    "fetch_done": "Analyzed {title}: {n} top post(s) out of {scanned} scanned.",
    # dashboard header
    "dash_download": "Export",
    "dash_fetched_at": "Fetched {when}",
    "dash_refresh": "🔄 Refresh",
    "dash_refresh_hint": "Lean refresh — re-scans only the months since this "
                         "channel was last fetched and merges them in, not "
                         "the whole stored period.",
    "dash_remove": "🗑 Remove",
    "dash_remove_confirm": "Remove “{name}” from the sidebar? "
                           "(The stored checkpoint file is deleted.)",
    "dash_period_label": "Period: {period}",
    "dash_created_label": "Created {when}",
    # stat cards
    "stat_members": "Members",
    "stat_total_posts": "Posts",
    "stat_total_posts_period": "Posts ({period})",
    "stat_avg_views": "Avg. views",
    "stat_posts_per_day": "Posts / day",
    "stat_avg_reactions": "Avg. reactions",
    "stat_avg_reposts": "Avg. reposts",
    "stat_created": "Created",
    # compare-mode calculated cards
    "stat_reposts_per_post": "Avg. repost / post",
    "stat_err_pct": "ERR%",
    "stat_err_pct_sub": "2 weeks+",
    "cmp_max_views": "Max views / post",
    "cmp_posts_per_day": "Post / day",
    "cmp_max_reposts": "Max repost / post",
    "cmp_avg_reactions": "Avg. reactions / post",
    "cmp_view_repost_year": "Views {year}",
    "cmp_erv_pct": "ERV%",
    "cmp_erv_pct_tip": "(Avg. reactions + avg. reposts) ÷ avg. views × 100% "
                       "— engagement rate by views.",
    "cmp_virality_index": "Virality index",
    "cmp_virality_index_tip": "Max views ÷ avg. views — the spread between the "
                              "best-performing post and the average. A high "
                              "value means the channel can occasionally produce "
                              "“viral hits” that far outperform its "
                              "normal content.",
    "cmp_viral_share": "Viral post share",
    "cmp_viral_share_tip": "The percentage of posts that received more than "
                           "2× the average number of views.",
    "dash_ethics_title": "Ethics",
    "dash_ethics_tip": "The Mentions view's own \"Ethics\" score, cached "
                      "here — fair (Telegram-linked) mentions as a share "
                      "of fair+fake, halved if uncredited \"No Link\" "
                      "mentions outnumber the fair ones. Not calculated "
                      "yet? Press Calculate below.",
    "dash_ethics_calc_btn": "Calculate",
    "dash_ethics_recalc_btn": "Recalculate",
    "dash_ethics_calculating": "Calculating…",
    "dash_ethics_none": "Not enough link data to calculate Ethics for this "
                        "channel — it may predate `all_links`, need a "
                        "re-fetch, or simply have no fair/fake mentions "
                        "in its stored history.",
    # charts
    "chart_trend_title": "Views / Reactions / Shares / Posts over time",
    "chart_quality": "Quality",
    "chart_posts": "Posts",
    "chart_trim_edges": "Hide first/last month",
    "chart_by_hour": "Posts by hour of day",
    "chart_by_weekday": "Posts by day of week",
    "dash_recent_posts_title": "Last 50 Posts",
    "chart_empty": "No data",
    # weekday short labels (Mon..Sun order)
    "wd_mon": "Mon", "wd_tue": "Tue", "wd_wed": "Wed", "wd_thu": "Thu",
    "wd_fri": "Fri", "wd_sat": "Sat", "wd_sun": "Sun",
    # top posts table (from channel_top)
    "top_posts_title": "Top posts",
    "top_viral_title": "Top viral posts",
    "dash_links_hint": "Generate a copyable text list of links to the "
                       "posts shown below.",
    "col_date": "Date",
    "col_post": "Post",
    "col_views": "Views",
    "col_reactions": "Reactions",
    "col_private": "Private reposts",
    "col_public": "Public reposts",
    "col_viral_rate": "Viral rate",
    "album_suffix": "  ·  album ({n} items)",
    "show": "Show",
    "public_na": "n/a",
    "public_off": "—",
    "public_title": "Public reposts of post #{id}",
    "public_empty": "No public reposts found for this post.",
    "public_col_channel": "Channel",
    "public_col_views": "Views",
    "public_col_link": "Link",
    # exports (kept from channel_top)
    "report_button": "📋 Text report",
    "report_empty": "Nothing to report yet.",
    "report_dialog_title": "Analytics report",
    "report_copy": "📋 Copy to clipboard",
    "report_title": "Analytics of channel {title}",
    "report_private": "Top 7 {emoji} Reposts",
    "report_views": "Top 7 {emoji} Views",
    "report_reactions": "Top 7 {emoji} Reactions",
    "save_md_button": "💾 Save MD",
    "md_saved": "Saved: {path}",
}

RU = {
    "app_title": "HypeStats",
    "nav_config": "⚙️ Настройки",
    "nav_no_channels": "Пока нет каналов — загрузите канал на экране «Настройки».",
    "nav_compare": "Метрики",
    "nav_compare_hint": "Выберите 2-8 каналов для сравнения.",
    "nav_sort_folders": "Сорт. по подпис.",
    "nav_sort_folders_active": "Сорт. по папкам",
    "nav_sort_folders_hint": "Группировать список каналов по папкам (в "
                            "порядке папок, без папки — в конце), внутри "
                            "каждой группы — по убыванию подписчиков, "
                            "вместо одного общего списка по подписчикам.",
    "nav_fold_hint": "Свернуть панель",
    "nav_unfold_hint": "Развернуть панель",
    "nav_lang_hint": "Переключить язык",
    "nav_compare_md_hint": "Экспортировать текущее сравнение в таблицу Markdown",
    "compare_title": "Сравнение каналов",
    "compare_md_metric": "Метрика",
    "compare_content_quality": "КАЧЕСТВО КОНТЕНТА",
    "folder_none": "Без папки",
    "folder_manage": "Управление папками…",
    "folder_manage_title": "Папки",
    "folder_add": "+ Добавить папку",
    "folder_name_prompt": "Название папки:",
    "folder_close": "Закрыть",
    "folder_pick_color": "Выбор цвета",
    "folder_change_color": "Изменить цвет",
    "folder_delete": "Удалить папку",
    "folder_delete_confirm": "Удалить папку «{name}»? Каналы в ней сохранятся — "
                             "будет удалена только привязка к папке.",
    "folder_section_title": "Папки",
    "folder_manage_help": "Создавайте, переименовывайте, перекрашивайте или "
                          "удаляйте папки для организации каналов в боковой панели.",
    "folder_list_empty": "Пока нет папок.",
    "folder_choose": "Добавить канал в папку",
    "folder_export_md_btn": "Экспорт в MD",
    "folder_export_md_hint": "Сохранить все отслеживаемые каналы в виде "
                             "таблицы Markdown, сгруппированные по папкам "
                             "(в порядке папок, без папки — в конце) и "
                             "отсортированные по подписчикам внутри "
                             "каждой группы.",
    "folder_export_col_folder": "Папка",
    "folder_export_col_followers": "Подписчики",
    "folder_export_col_id": "ID/Юзернейм",
    "folder_export_extra_cols": "Рейтинг, просмотры, доля виральных, "
                                "качество постов",
    "folder_export_extra_cols_hint": "Добавить колонки Рейтинг (составной "
                                     "балл, формула как в Статистике "
                                     "папки), Просмотры (сумма за период), "
                                     "Доля виральных и Качество постов "
                                     "(средний балл) — читает полный "
                                     "чекпоинт каждого канала, а не только "
                                     "сводку из боковой панели.",
    "folder_export_period_hint": "За какой период считать Рейтинг/"
                                 "Просмотры/Долю виральных — последнее "
                                 "полугодие или сезон канала, либо вся "
                                 "статистика за всё время.",
    "folder_export_ethics_chk": "Рассчитать Этику",
    "folder_export_ethics_hint": "Перед экспортом (пере)рассчитать Этику "
                                 "(см. собственную карточку «Этика» "
                                 "вида «Упоминания») для каждого канала в "
                                 "выборке и закешировать в его чекпоинт — "
                                 "сначала запускается фоновая задача, "
                                 "так как она сканирует ссылки во всех "
                                 "просканированных постах. Оставьте "
                                 "не отмеченным, чтобы просто "
                                 "экспортировать уже закешированное "
                                 "(пусто для канала, для которого это "
                                 "никогда не рассчитывалось).",
    "folder_export_ethics_folder_hint": "Для каких каналов "
                                        "(пере)рассчитать Этику.",
    "folder_export_ethics_all": "Все каналы",
    "folder_export_ethics_tags_chk": "Только с тегами",
    "folder_export_ethics_tags_hint": "Дополнительно сузить выборку выше "
                                      "до каналов, у которых уже есть "
                                      "тег (любой).",
    "folder_export_col_ethics": "Этика",
    "folder_export_rating_trend_note": "_«Рейтинг» снижается для канала, чья "
                                       "активность год к году замедляется "
                                       "(−20%), затухает (−33%) или заброшена "
                                       "(−50%)._",
    "folder_export_alltime_note": "_«Этика» считается по всей распарсенной "
                                  "истории канала; период выше влияет только на "
                                  "«Рейтинг» / «Просмотры» / «Долю виральных» / "
                                  "«Качество постов»._",
    "folder_mentions_refresh_label": "Обновить ссылки для:",
    "folder_mentions_refresh_btn": "🔗 Обновить ссылки",
    "folder_mentions_refresh_hint": "Заново получить только ссылки для "
                                    "каждого уже текстового сохранённого "
                                    "поста в каналах этой папки — намного "
                                    "быстрее полной перезагрузки, так как "
                                    "остальное не трогает.",
    "folder_comments_refresh_label": "Обновить комментарии для:",
    "folder_comments_refresh_btn": "💬 Обновить комментарии",
    "folder_comments_refresh_hint": "Заново получить только число "
                                    "комментариев для каждого сохранённого "
                                    "поста в каналах этой папки — намного "
                                    "быстрее полной перезагрузки, так как "
                                    "остальное не трогает.",
    "folder_assign_all_label": "Назначить все каналы в:",
    "folder_assign_all_btn": "Назначить все",
    "folder_assign_all_hint": "Переместить все отслеживаемые каналы в эту "
                              "папку, заменив текущую папку у каждого.",
    "folder_assign_all_confirm": "Переместить все {count} каналов в "
                                 "«{folder}»? Это заменит папку, в которой "
                                 "они сейчас находятся.",
    "folder_assign_all_none": "Пока нет каналов для назначения — сначала "
                              "загрузите канал на этом экране.",
    # lean refresh (список устаревших + массовое обновление на экране настроек)
    "lean_refresh_title": "Лёгкое обновление",
    "lean_refresh_help": "Обновить устаревшие данные без полного пересканирования "
                         "— лёгкое обновление перечитывает только месяцы, "
                         "прошедшие с последней загрузки канала, и досливает "
                         "их. Прогресс и лог — в панели загрузки выше.",
    "lean_refresh_oldest_btn": "Старые 10",
    "lean_refresh_oldest_hint": "Обновить 10 каналов с самой давней датой "
                                "обновления.",
    "lean_refresh_1mo_btn": "1 мес+",
    "lean_refresh_1mo_hint": "Обновить все каналы, не обновлявшиеся более месяца.",
    "lean_refresh_3mo_btn": "3 мес+",
    "lean_refresh_3mo_hint": "Обновить все каналы, не обновлявшиеся более 3 "
                             "месяцев.",
    "lean_refresh_selected_btn": "Обновить выбранные",
    "lean_refresh_selected_hint": "Обновить только каналы, отмеченные в списке ниже.",
    "lean_refresh_selected_2y_btn": "Пересканировать выбранные · 2 г",
    "lean_refresh_selected_2y_hint": "Полное пересканирование (не инкрементное) "
                                     "отмеченных каналов за последние 2 года — "
                                     "перестраивает все месяцы с текущими "
                                     "полями постов (репосты, кнопки, "
                                     "комментарии). Медленнее лёгкого обновления.",
    "lean_refresh_selected_all_btn": "Пересканировать выбранные · Всё время",
    "lean_refresh_selected_all_hint": "Полное пересканирование (не "
                                      "инкрементное) отмеченных каналов за "
                                      "всю историю — перестраивает все "
                                      "месяцы с текущими полями постов "
                                      "(репосты, кнопки, комментарии). "
                                      "Гораздо медленнее пересканирования за "
                                      "2 года для канала с долгой историей.",
    "lean_refetch_mentions_btn": "🔗 Обновить упоминания",
    "lean_refetch_mentions_hint": "Заново получить только ссылки для уже "
                                  "текстовых сохранённых постов отмеченных "
                                  "каналов — та же задача, что и строка "
                                  "«Обновить ссылки для:» выше, но для "
                                  "отмеченных каналов вместо целой папки.",
    "mentions_refresh_confirm": "Обновить ссылки упоминаний для {count} канал(ов)?",
    "lean_refresh_confirm": "Обновить {count} канал(ов)?",
    "lean_refresh_full_confirm": "Полное пересканирование {count} канал(ов) за "
                                 "последние 2 года? Медленнее лёгкого обновления.",
    "lean_refresh_all_confirm": "Полное пересканирование {count} канал(ов) за "
                               "ВСЮ историю? Это может занять много времени и "
                               "заметно расходовать лимиты API для канала с "
                               "многолетней историей.",
    "lean_refresh_none": "Нет настолько старых — все каналы свежее.",
    "lean_refresh_none_selected": "Отметьте в списке хотя бы один канал.",
    "lean_refresh_empty": "Пока нет отслеживаемых каналов.",
    "lean_refresh_col_updated": "Обновлён",
    "lean_refresh_col_channel": "Канал",
    # Карточка «Упоминания» на экране настроек (Folders & Tags, под
    # карточками Папки/Теги) — пакетный экспорт Отчёта по ссылкам/Отчёта
    # по упоминаниям для всей папки, см. tools.mentions_export.
    "mentions_export_title": "Упоминания",
    "mentions_export_help": "Экспортировать Отчёт по ссылкам или Отчёт по "
                            "упоминаниям из вида «Упоминания» сразу для "
                            "всех каналов папки — один Markdown-файл, "
                            "разделы по названию канала.",
    "mentions_export_folder_label": "Папка:",
    "mentions_export_link_btn": "Все отчёты по ссылкам",
    "mentions_export_link_hint": "Отчёт по ссылкам (честные/ложные/"
                                 "неразрешённые/промо-ссылки, сначала "
                                 "самые повторяющиеся) для каждого канала "
                                 "выбранной папки, в одном Markdown-файле.",
    "mentions_export_mentions_btn": "Все отчёты по упоминаниям",
    "mentions_export_mentions_hint": "Отчёт по упоминаниям (каждое "
                                     "собранное имя, со ссылкой или без) "
                                     "для каждого канала выбранной папки, "
                                     "в одном Markdown-файле.",
    "folder_export_col_tag": "Тег",
    # tags (карточка «Теги» на экране настроек, значки в боковой панели,
    # кнопка тега в дашборде)
    "tag_section_title": "Теги",
    "tag_manage_help": "Загрузите таблицу Markdown (| tag | long tag | "
                       "description |), чтобы задать доступные теги. "
                       "Изменение tags.md и повторная загрузка заменяют "
                       "весь список — назначить тег каналу можно из "
                       "контекстного меню в боковой панели или кнопкой "
                       "тега в дашборде.",
    "tag_list_empty": "Пока нет загруженных тегов.",
    "tag_load_md_btn": "Загрузить MD",
    "tag_load_md_hint": "Выберите файл Markdown в формате "
                        "«| tag | long tag | description |» — заменяет "
                        "текущий список тегов; каналы с удалённым тегом "
                        "останутся без тега.",
    "tag_load_md_done": "Загружено тегов: {n}.",
    "tag_load_md_empty": "В этом файле не найдено тегов — проверьте формат "
                         "«| tag | long tag | description |».",
    "tag_none": "Без тега",
    "tag_choose": "Назначить этому каналу тег",
    # folder stat view
    "nav_folder_stat": "📁 Папки и теги",
    "folder_stat_sub": "Управление папками и тегами, а также статистика по "
                       "периодам и сводный рейтинг для одной папки",
    "folder_stat_pick_folder": "Папка:",
    "folder_stat_no_folders": "Пока нет папок — создайте на экране настроек.",
    "folder_stat_empty_channels": "В этой папке пока нет каналов.",
    "folder_stat_links_title": "Связи каналов",
    "folder_stat_links_hint": "Репосты между каналами этой папки, определённые "
                              "по топ-постам каждого канала. Требует включённой "
                              "опции «Публичные репосты» при сборе канала.",
    "folder_stat_links_empty": "Репостов между каналами не найдено.",
    "folder_stat_period_title": "Статистика по периодам",
    "folder_stat_period_hint": "Просмотры/репосты/реакции/доля виральных по "
                               "периодам и показанный пост считаются по "
                               "полной истории постов каждого канала (для "
                               "старых checkpoint'ов нужен повторный сбор). "
                               "Колонки «Имя» и «Сайт» оставлены пустыми — "
                               "заполните их позже.",
    "period_mode_month": "По месяцам",
    "period_mode_season": "По сезонам",
    "period_mode_halfyear": "По полугодиям",
    "period_mode_year": "Год",
    "period_year_half": "Полгода",
    "period_year_1y": "Последний год",
    "period_year_1_5y": "1,5 года",
    "period_year_2y": "2 года",
    "period_year_all": "Всё загруженное время",
    "col_channel_title": "Название",
    "col_username_id": "Username/ID",
    "col_shares": "Репосты",
    "col_most_viewed": "Самый популярный пост",
    "col_viral_share": "Доля виральных",
    "col_rating": "Рейтинг",
    "col_post_quality": "Качество постов",
    "folder_stat_period_empty": "Постов в этой папке не найдено.",
    # mutual pr view
    "nav_mutual_pr": "🤝 Взаимопиар",
    "mutual_pr_sub": "Сравнение отслеживаемых каналов для взаимопиара: охват, "
                     "качество и оценка прироста подписчиков от рекламного поста.",
    "mutual_pr_hint": "Колонки прогноза и лучших дней — это оценки, а не "
                      "измерения: в приложении нет реальных данных об "
                      "исходах рекламных кампаний. Они учитывают качество "
                      "недавнего контента канала, которое считается только "
                      "по тому объёму постов за последние ~3 месяца, что "
                      "сохранён в checkpoint'е, плюс фиксированную кривую "
                      "набора просмотров и условный коэффициент конверсии "
                      "охвата в подписчиков (см. app.scoring_pr). "
                      "Воспринимайте их как повод для разговора, а не как "
                      "гарантию.",
    "mutual_pr_pick_folder": "Папка:",
    "mutual_pr_all_channels": "Все каналы",
    "mutual_pr_empty": "Пока нет отслеживаемых каналов — соберите канал на "
                       "экране настроек.",
    "mutual_pr_range_tooltip": "Примерный диапазон: {low}–{high} (грубая оценка "
                               "неопределённости, не статистический интервал — "
                               "см. app.scoring_pr)",
    "col_followers": "Подписчики",
    "col_forecast_24h": "Прогноз 24ч",
    "col_repeated_after_month": "Повтор через месяц",
    "col_forecast_48h": "Прогноз 48ч",
    "col_forecast_72h": "Прогноз 72ч",
    "col_forecast_week": "Прогноз неделя",
    "col_forecast_month": "Прогноз месяц",
    "col_best_days": "Лучшие дни для поста",
    "activity_verdict_active": "активен",
    "activity_verdict_slowing": "замедляется",
    "activity_verdict_stalling": "затухает",
    "activity_verdict_abandoned": "заброшен",
    "mutual_pr_activity_tooltip": "Активность: {verdict} (прогноз снижен на {pct}%)",
    # mutual pr — таблица репостов между каналами (перенесена из «Статистики папки»)
    "mutual_pr_links_title": "Связи каналов",
    "mutual_pr_links_hint": "Какие отслеживаемые каналы уже ссылаются друг на "
                            "друга — по каждой ссылке t.me в подписи поста, а "
                            "не только по официальной статистике "
                            "«публичных репостов» Telegram, так что работает "
                            "даже без включённой опции «Публичные репосты». "
                            "Кешируется: нажмите «Рассчитать»/«Пересчитать», "
                            "чтобы просканировать всю базу заново.",
    "mutual_pr_links_empty": "Связей между каналами не найдено — нажмите "
                             "«Рассчитать» либо расширьте фильтр по папке "
                             "выше.",
    "mutual_pr_links_calc_btn": "Рассчитать",
    "mutual_pr_links_recalc_btn": "Пересчитать",
    "mutual_pr_links_calculating": "Расчёт…",
    "mutual_pr_links_calculated_at": "Рассчитано {when}",
    "mutual_pr_links_col_channel": "Канал",
    "mutual_pr_links_col_by": "Упомянут",
    "mutual_pr_links_col_count": "Упоминания",
    "mutual_pr_links_col_top_source": "Основной источник",
    "mutual_pr_links_also": "Также упоминают: {names}",
    # mutual pr — «Пары ВП» (карточка + экспорт Markdown)
    "mutual_pr_partners_title": "Пары ВП",
    "mutual_pr_partners_hint": "«Лучшие дни для поста» — свои лучшие дни "
                               "каждого канала (A / B); ★ отмечает дни, "
                               "удобные для рекламы сразу в обоих каналах. "
                               "Исключены пары, уже показанные связанными в "
                               "карточке «Связи каналов» выше, и оставлено не "
                               "более 16 лучших пар для каждого канала, чтобы "
                               "один канал-универсал не вытеснял остальные.",
    "mutual_pr_partners_empty": "Пока нет пар каналов для оценки.",
    "mutual_pr_partners_col_a": "Канал A",
    "mutual_pr_partners_col_b": "Канал B",
    "mutual_pr_partners_col_days": "Лучшие дни для поста",
    "mutual_pr_partners_col_forecast": "Прогноз 24ч (A / B)",
    # high-quality posts view
    "nav_content_quality": "🎯 Лучшие посты",
    "cqi_fetch_media": "🖼 Загрузить медиа",
    "cqi_fetch_media_running": "Загрузка…",
    "cqi_fetch_media_hint": "Загрузить маленькое превью для каждого поста "
                            "ниже (загружаются только те, для которых ещё "
                            "нет кэша)",
    "cqi_fetch_media_need_login": "Сначала добавьте данные Telegram API на "
                                  "экране настроек.",
    "cqi_fetch_media_all_cached": "У всех показанных постов уже есть "
                                  "кэшированное превью — загружать нечего.",
    "cqi_fetch_media_login_required": "Нужен вход в Telegram — сначала "
                                      "войдите на экране настроек, затем "
                                      "попробуйте снова.",
    "cqi_empty_posts": "Постов в этой папке за этот период не найдено.",
    "cqi_post_tooltip": "{label}\nОценка: {score}",
    "cqi_post_tooltip_formula": "вес реакций = min(реакции,{t1cap})×{t1w} "
                                "+ max(0, min(реакции,{t2cap})−{t1cap})×{t2w}\n"
                                "           = min({reactions},{t1cap})×{t1w} "
                                "+ max(0, min({reactions},{t2cap})−{t1cap})×{t2w}\n"
                                "           = {reaction_weighted}\n\n"
                                "виральный избыток = max(0, просмотры − "
                                "среднее по каналу) = max(0, {views} − "
                                "{avg_views}) = {viral_excess}\n\n"
                                "ERV% = (репосты×{fwd_w} + комментарии(≤100)×{cmt_w} "
                                "+ вес реакций + виральный избыток×{vrl_w}) "
                                "/ просмотры × 100\n"
                                "     = ({forwards}×{fwd_w} + {comments}×{cmt_w} "
                                "+ {reaction_weighted} + {viral_excess}×{vrl_w}) "
                                "/ {views} × 100\n"
                                "     = {erv}%\n"
                                "сырой балл = ERV% × 100 = {raw}\n"
                                "шкала = сырой / (сырой + {k}) × 1000 = {gauge}",
    "cqi_tg_links": "🅰️ Ссылки",
    "cqi_tg_links_hint": "Сформировать копируемый текстовый список ссылок "
                         "на посты ниже, по убыванию оценки.",
    "cqi_tg_links_dialog_title": "Ссылки",
    "cqi_tg_links_header": "{folder} — лучшие посты за {period}",
    "cqi_tg_links_top_authors_title": "Лучшие {n} авторов",
    "cqi_export_md_btn": "MD",
    "cqi_export_md_hint": "Сохранить показанные посты как таблицу Markdown "
                          "с превью каждого поста, встроенным прямо в "
                          "файл (base64) — не требует внешних файлов, в "
                          "отличие от списка ссылок.",
    "cqi_md_col_channel": "Канал",
    "cqi_md_col_score": "Балл CQI",
    "cqi_md_col_thumbnail": "Превью",
    "cqi_md_col_media": "Медиа",
    "cqi_md_col_text": "Текст",
    "cqi_md_col_link": "Ссылка",
    "cqi_md_hitmakers_title": "Топ {n} лучших авторов папки «{folder}»",
    "cqi_all_folders": "Все папки",
    "cqi_max_posts_n": "Топ {n}",
    "cqi_max_posts_hint": "Сколько постов показывать всего — в сетке, "
                         "лимите по каналу и списке ссылок.",
    "cqi_tg_links_limit_hint": "Ограничить число постов одного канала ниже "
                               "и в списке ссылок. «Ограничить доминирующий "
                               "канал» не задаёт фиксированный лимит, но в "
                               "списке Топ 50+ обрезает любой канал, "
                               "занимающий более 11 мест и 3× доли обычного "
                               "канала, до 11.",
    "cqi_tg_links_limit_none": "Без ограничения",
    "cqi_tg_links_limit_anomaly": "Ограничить доминирующий канал",
    "cqi_tg_links_limit_n": "{n} с канала",
    "cqi_hide_non_media": "Скрыть посты без медиа",
    "cqi_hide_non_media_hint": "Скрыть текстовые посты (без фото, видео, "
                               "голосового/аудио или другого файла) из "
                               "сетки, лимита по каналу и списка ссылок.",
    "cqi_min_followers_none": "Любые подписчики",
    "cqi_min_followers_n": "≥{n} подписчиков",
    "cqi_min_followers_hint": "Учитывать в рейтинге только посты каналов "
                              "минимум с таким числом подписчиков — иначе "
                              "маленький, но активный канал может "
                              "вытеснить посты более крупных каналов.",
    # compare charts view
    "nav_compare_charts": "Графики",
    "nav_compare_charts_hint": "Выберите до 8 каналов для сравнения на графиках.",
    "compare_charts_sub": "Просмотры, репосты и реакции по времени для "
                          "каналов, выбранных в боковой панели",
    # mentions view
    "nav_mentions": "Упоминания",
    "nav_mentions_hint": "Выберите до 4 каналов, чтобы сравнить тексты постов "
                         "и упомянутые в них имена.",
    "mentions_no_extraction": "Извлечение имён недоступно (transformers не "
                              "установлен или не удалось загрузить модель) — "
                              "тексты постов всё равно показаны ниже, но имена "
                              "из них извлекаться не будут.",
    "mentions_period_label": "Период упоминаний:",
    "mentions_period_all": "Всё время",
    "mentions_reload_btn": "Обновить",
    "mentions_reload_hint": "Перечитать mentions.md с диска",
    "mentions_reload_confirm": "Отменить несохранённые изменения и "
                               "перезагрузить mentions.md с диска?",
    "mentions_empty": "Выберите до 4 каналов в режиме «Упоминания» боковой панели.",
    "mentions_names_title_counts": "Постов {posts}/{total_posts}, найдено "
                                   "имён: {total}, в mentions.md {linked}",
    "mentions_links_title_counts": "Постов {posts}/{total_posts}, "
                                   "ссылок в Telegram {tg}, веб-ссылок {web}, ",
    "mentions_links_title_most": "Чаще всего ({count}): ",
    "mentions_links_title_none": "ссылок нет",
    "mentions_stats_classifying": "Классификация ссылок…",
    "mentions_stats_balance_value": "{tg}%/{web}%",
    "mentions_stats_none": "—",
    "mentions_card_fairness": "Этика",
    "mentions_card_fair": "Честные",
    "mentions_card_fake": "Ложные",
    "mentions_card_no_link": "Без ссылки",
    "mentions_card_unique": "Уникальные",
    "mentions_card_tg_web": "Tg / Веб",
    "mentions_stats_report_row": "Отчёт по ссылкам ({count}) →",
    "mentions_stats_unresolved_row": "Неразрешённые честные ссылки ({count}) →",
    "mentions_report_row": "Отчёт по упоминаниям ({count}) →",
    "mentions_report_title": "Отчёт по упоминаниям — {channel}",
    "mentions_report_no_link": "Без ссылки",
    "mentions_stats_report_title": "Отчёт по ссылкам — {channel}",
    "mentions_stats_report_empty": "В выборке нет классифицированных ссылок.",
    "mentions_stats_report_col_status": "Статус",
    "mentions_stats_report_col_count": "Кол-во",
    "mentions_stats_report_col_url": "Ссылка",
    "mentions_stats_report_name_hint": "Открыть пост, откуда это извлечено",
    "mentions_open_link_btn": "Открыть",
    "mentions_source_btn": "Источник",
    "mentions_stats_report_sources_title": "Посты-источники",
    "mentions_link_status_fair": "Честная",
    "mentions_link_status_fake": "Ложная",
    "mentions_link_status_unresolved": "Не разрешена",
    "mentions_link_status_promo": "Промо",
    "mentions_stats_unresolved_title": "Неразрешённые честные ссылки — {channel}",
    "mentions_stats_unresolved_empty": "Нечего связывать — каждое имя со "
                                       "ссылкой Telegram уже есть в "
                                       "mentions.md.",
    "mentions_stats_unresolved_col_link": "Ссылка",
    "mentions_similar_title": "Похожие упоминания",
    "mentions_similar_md_btn": "Похожие MD",
    "mentions_similar_md_hint": "Экспорт людей, которых упоминают два и более "
                                "загруженных канала, в Markdown: варианты "
                                "имени и по колонке на канал со ссылками на "
                                "посты.",
    "mentions_similar_md_empty": "Нет имён, общих для двух и более загруженных "
                                 "каналов.",
    "mentions_similar_md_col_names": "Варианты имени",
    "mentions_table_title": "mentions.md",
    "mentions_locate_btn": "Показать в папке",
    "mentions_locate_hint": "Показать mentions.md в Finder/Проводнике",
    "mentions_add_row_btn": "+ Строка",
    "mentions_save_btn": "Сохранить",
    "mentions_col_id": "id",
    "mentions_col_names": "имена",
    "mentions_col_links": "неясные ссылки",
    "mentions_col_name": "Имя",
    "mentions_col_found": "mentions.md",
    "mentions_col_posts": "Посты",
    "mentions_col_post_id": "ID",
    "mentions_col_date": "Дата",
    "mentions_col_type": "Тип",
    "mentions_col_text": "Текст",
    "mentions_forwarded_from": "Переслано от {name}",
    "mentions_forwarded": "Переслано",
    "mentions_link_btn": "Связать…",
    "mentions_link_tg_hint": "Подтверждено настоящей ссылкой Telegram в "
                            "посте — увереннее, чем просто извлечение текста",
    "mentions_mark_btn": "Отметить…",
    "mentions_mark_hint": "Извлечение имён ничего не нашло в этом посте — "
                          "введите имя вручную, чтобы связать его",
    "mentions_ignore_btn": "Игнорировать",
    "mentions_ignore_hint": "Не имя — больше не извлекать",
    "mentions_link_new": "+ Новый id…",
    "mentions_new_id_prompt": "Юзернейм канала @username или полное имя "
                              "(ФИО) этого человека:",
    "mentions_confirm_name_title": "Подтвердите имя",
    "mentions_confirm_name_prompt": "Текст для добавления (исправьте, если "
                                    "извлечение ошиблось):",
    "mentions_delete_row_hint": "Удалить эту строку из mentions.md",
    "mentions_delete_row_confirm": "Удалить «{id}» из mentions.md?",
    "compare_charts_empty": "Выберите до 8 каналов в боковой панели, чтобы "
                            "построить графики.",
    "menu_file": "Файл",
    "menu_language": "Язык",
    "menu_theme": "Тема",
    "import_env": "Импортировать .env в текущий профиль…",
    "export_env": "Экспортировать профиль как .env…",
    "open_config_folder": "Открыть папку настроек",
    "quit": "Выход",
    "lang_en": "English",
    "lang_ru": "Русский",
    "theme_system": "Как в системе",
    "theme_light": "Светлая",
    "theme_dark": "Тёмная",
    "theme_black": "Чёрная (AMOLED)",
    "zoom_label": "Размер интерфейса",
    "zoom_small": "Мелкий (−2 пт, заголовки −3)",
    "zoom_standard": "Стандартный",
    "zoom_large": "Крупный (+2 пт, заголовки +1)",
    "accent_label": "Акцентный цвет",
    "accent_default": "По умолчанию",
    "accent_custom": "Свой цвет…",
    "env_imported": "Импортировано значений из .env: {n}.",
    "save": "💾 Сохранить профиль",
    "saved": "Профиль сохранён.",
    "stop": "⏹ Остановить",
    "done_ok": "✅ Готово: {msg}",
    "done_fail": "❌ Ошибка: {msg}",
    "cancelled": "⏹ Остановлено пользователем.",
    "missing_conn": "Сначала заполните API_ID, API_HASH и PHONE_NUMBER.",
    "worker_running": "Загрузка ещё выполняется. Сначала остановите её.",
    "profile": "Профиль",
    "new_profile": "Новый…",
    "delete_profile": "Удалить",
    "profile_name": "Имя профиля:",
    "delete_profile_confirm": "Удалить профиль «{name}»?",
    "field_API_ID": "API_ID",
    "field_API_HASH": "API_HASH",
    "field_PHONE_NUMBER": "PHONE_NUMBER",
    "config_location": "Файл настроек: {path}",
    "instructions_title": "📖 Как получить API_ID и API_HASH",
    "instructions_text": (
        "1. Откройте <a href='https://my.telegram.org'>my.telegram.org</a> и войдите "
        "по номеру телефона (код придёт в приложение Telegram).<br>"
        "2. Нажмите <b>API development tools</b>.<br>"
        "3. Заполните <i>App title</i> и <i>Short name</i> (любой текст), "
        "платформа <i>Desktop</i>, создайте приложение.<br>"
        "4. Скопируйте <b>App api_id</b> → API_ID и <b>App api_hash</b> → API_HASH.<br>"
        "5. PHONE_NUMBER — в международном формате, например <code>+79001234567</code>.<br><br>"
        "<b>Какой канал можно анализировать?</b> Любой публичный по "
        "<code>@username</code> или приватный, где вы состоите — по его "
        "<code>-100…</code> ID или ссылке t.me."
    ),
    "login_code_prompt": "Введите код входа, который прислал Telegram:",
    "login_password_prompt": "Введите пароль двухфакторной аутентификации (2FA):",
    "login_title": "Вход в Telegram",
    "qr_login_button": "🔳 Вход по QR-коду",
    "qr_login_title": "Вход по QR-коду",
    "qr_login_hint": "Откройте Telegram на телефоне → Настройки → Устройства → "
                     "Подключить устройство и отсканируйте этот код.",
    "qr_login_generating": "Генерация QR-кода…",
    "qr_login_waiting": "Ожидание сканирования…",
    "qr_login_expired": "Код истёк, генерируется новый…",
    "qr_login_success": "Вход выполнен успешно.",
    "check_login_button": "🔎 Проверить вход",
    "check_login_checking": "Проверка…",
    "check_login_ok": "✅ Вход выполнен как {name} (+{phone}).",
    "check_login_not_authorized": "❌ Не выполнен вход — используйте QR-код.",
    "fetch_title": "Загрузить канал",
    "fetch_help": "Сканирует публичный/приватный канал за выбранный период: "
                  "читает просмотры, реакции и репосты каждого поста, отбирает "
                  "лучшие и считает статистику активности (участники, "
                  "постов/день, паттерны по часам и дням недели). Результат "
                  "сохраняется как контрольная точка и добавляется в боковую "
                  "панель.",
    "fetch_channel": "ID канала или @username",
    "fetch_channel_placeholder": "@durov или -1001234567890",
    "fetch_period": "Период анализа",
    "period_3m": "3 месяца",
    "period_6m": "6 месяцев",
    "period_1y": "1 год",
    "period_2y": "2 года",
    "period_3y": "3 года",
    "period_all": "Всё время",
    "fetch_public": "Запросить публичные репосты (медленнее, нужен доступ к статистике)",
    "fetch_button": "📊 Загрузить и проанализировать",
    "fetch_log": "Журнал",
    "fetch_done": "Проанализирован {title}: {n} лучших постов из {scanned}.",
    "dash_download": "Экспорт",
    "dash_fetched_at": "Загружено {when}",
    "dash_refresh": "🔄 Обновить",
    "dash_refresh_hint": "Лёгкое обновление — перечитывает только месяцы, "
                         "прошедшие с последней загрузки канала, и досливает "
                         "их, а не весь сохранённый период.",
    "dash_remove": "🗑 Удалить",
    "dash_remove_confirm": "Убрать «{name}» из боковой панели? "
                           "(Файл контрольной точки будет удалён.)",
    "dash_period_label": "Период: {period}",
    "dash_created_label": "Создан {when}",
    "stat_members": "Участники",
    "stat_total_posts": "Постов",
    "stat_total_posts_period": "Постов ({period})",
    "stat_avg_views": "Ср. просмотры",
    "stat_posts_per_day": "Постов / день",
    "stat_avg_reactions": "Ср. реакции",
    "stat_avg_reposts": "Ср. репосты",
    "stat_created": "Создан",
    "stat_reposts_per_post": "Ср. репост / пост",
    "stat_err_pct": "ERR%",
    "stat_err_pct_sub": "2 нед.+",
    "cmp_max_views": "Макс. просмотры / пост",
    "cmp_posts_per_day": "Постов / день",
    "cmp_max_reposts": "Макс. репост / пост",
    "cmp_avg_reactions": "Ср. реакции / пост",
    "cmp_view_repost_year": "Просмотры {year}",
    "cmp_erv_pct": "ERV% (действия/просмотры)",
    "cmp_erv_pct_tip": "(Ср. реакции + ср. репосты) ÷ ср. просмотры × 100% "
                       "— вовлечённость по просмотрам.",
    "cmp_virality_index": "Индекс виральности",
    "cmp_virality_index_tip": "Макс. просмотры ÷ ср. просмотры — разброс между "
                              "лучшим постом и средним. Высокое значение "
                              "значит, что канал способен иногда выдавать "
                              "«вирусные» посты, значительно превосходящие "
                              "обычный контент.",
    "cmp_viral_share": "Доля вирусных постов",
    "cmp_viral_share_tip": "Процент постов, набравших более чем в 2 раза "
                           "больше среднего числа просмотров.",
    "dash_ethics_title": "Этика",
    "dash_ethics_tip": "Собственная «Этика» из вида «Упоминания», "
                      "кешированная здесь — доля честных (со ссылкой в "
                      "Telegram) упоминаний среди честных+ложных, "
                      "уменьшенная вдвое, если неотмеченных «Без ссылки» "
                      "упоминаний больше, чем честных. Ещё не "
                      "рассчитано? Нажмите «Рассчитать» ниже.",
    "dash_ethics_calc_btn": "Рассчитать",
    "dash_ethics_recalc_btn": "Пересчитать",
    "dash_ethics_calculating": "Расчёт…",
    "dash_ethics_none": "Недостаточно данных по ссылкам, чтобы "
                        "рассчитать Этику для этого канала — возможно, "
                        "он старше поля `all_links`, нужно "
                        "пересканировать, или в сохранённой истории "
                        "просто нет честных/ложных упоминаний.",
    "chart_trend_title": "Просмотры / Реакции / Репосты / Посты по времени",
    "chart_quality": "Качество",
    "chart_posts": "Посты",
    "chart_trim_edges": "Скрыть первый/последний месяц",
    "chart_by_hour": "Посты по часам суток",
    "dash_recent_posts_title": "Последние 50 постов",
    "chart_by_weekday": "Посты по дням недели",
    "chart_empty": "Нет данных",
    "wd_mon": "Пн", "wd_tue": "Вт", "wd_wed": "Ср", "wd_thu": "Чт",
    "wd_fri": "Пт", "wd_sat": "Сб", "wd_sun": "Вс",
    "top_posts_title": "Топ постов",
    "top_viral_title": "Топ виральных постов",
    "dash_links_hint": "Сформировать копируемый текстовый список ссылок "
                       "на посты ниже.",
    "col_date": "Дата",
    "col_post": "Пост",
    "col_views": "Просмотры",
    "col_reactions": "Реакции",
    "col_private": "Личные репосты",
    "col_viral_rate": "Виральность",
    "col_public": "Публичные репосты",
    "album_suffix": "  ·  альбом ({n} элементов)",
    "show": "Показать",
    "public_na": "н/д",
    "public_off": "—",
    "public_title": "Публичные репосты поста #{id}",
    "public_empty": "Публичных репостов этого поста не найдено.",
    "public_col_channel": "Канал",
    "public_col_views": "Просмотры",
    "public_col_link": "Ссылка",
    "report_button": "📋 Текстовый отчёт",
    "report_empty": "Пока нечего показывать.",
    "report_dialog_title": "Аналитический отчёт",
    "report_copy": "📋 Скопировать",
    "report_title": "Аналитика канала {title}",
    "report_private": "Топ 7 {emoji} Репостов",
    "report_views": "Топ 7 {emoji} Просмотров",
    "report_reactions": "Топ 7 {emoji} Реакций",
    "save_md_button": "💾 Сохранить MD",
    "md_saved": "Сохранено: {path}",
}

LANGS = {"en": EN, "ru": RU}


class I18n:
    def __init__(self, lang: str = "en") -> None:
        self.lang = lang if lang in LANGS else "en"

    def tr(self, key: str, **kw) -> str:
        text = LANGS[self.lang].get(key) or EN.get(key) or key
        return text.format(**kw) if kw else text

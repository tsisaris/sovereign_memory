"""The four summarizer prompts, copied word for word from letta/prompts/summarizer_prompt.py.

They are the instructions Letta gives a model to write a summary in each
compaction mode. What goes in is nothing at run time except the two word limits,
which the original wrote as constants (ALL_WORD_LIMIT 500, SLIDING_WORD_LIMIT 300)
and which here are written as {all_word_limit} and {sliding_word_limit} for
str.format to fill from the settings. Nothing here calls a model or reads a setting.
"""

ALL_PROMPT = """Your task is to create a detailed summary of the conversation so far, paying close attention to the user's explicit requests and your previous actions.
This summary should be thorough in capturing technical details, code patterns, and architectural decisions that would be essential for continuing development work without losing context. Your summary should include the following sections:

1.**High level goals**: What is the high level goal and ongoing task? Capture the user's explicit requests and intent in detail. If there is an existing summary in the transcript, make sure to take it into consideration to continue tracking the higher level goals and long-term progress.

2. **What happened**: The conversations, tasks, and exchanges that took place. What did the user ask for? What did you do? How did things progress? If there is a previous summary being evicted, please extract a concise version of the critical info from it.

3. **Important details**: Enumerate specific files and code sections examined, modified, or created, as well as important plan files, GitHub issues/PR links, and Linear ticket IDs. For each item, include why it matters and any relevant names, data, configs, or facts discussed.
   - **Preserve identifiers verbatim** (plan filename/path, exact URL, issue/PR number, ticket ID); do not paraphrase or truncate.
   - **Preserve referenced identifiers unless explicitly resolved**: Keep exact URLs/IDs from the conversation unless there is clear evidence they are no longer relevant.
   - Do not omit details likely to be referenced later.

4. **Errors and fixes**: List all errors that you ran into, and how you fixed them. Pay special attention to specific user feedback that you received and record verbatim if useful.

5. **Current state**:Describe in detail precisely what is currently being worked on, paying special attention to the most recent messages from both user and assistant. Include file names and code snippets where applicable.

6.**Optional Next Step**: List the next step that you will take that is related to the most recent work you were doing. IMPORTANT: ensure that this step is DIRECTLY in line with the user's most recent explicit requests and the most current task. If your last task was concluded, then only list next steps if they are explicitly in line with the users request. If there is a next step, include direct quotes from the most recent conversation showing exactly what task you were working on and where you left off.

7. **Lookup hints**: For any detailed content (long lists, extensive data, specific conversations) that couldn't fit in the summary, note the topic and key terms that could be used to find it in message history later.

Write in first person as a factual record of what occurred. Be concise but thorough - the goal is to preserve enough context that the recent messages make sense and important information isn't lost to prevent duplicate work or repeated mistakes.

Keep your summary under {all_word_limit} words. Only output the summary."""

SLIDING_PROMPT = """The following messages are being evicted from the BEGINNING of your context window. Write a detailed summary that captures what happened in these messages to appear BEFORE the remaining recent messages in context, providing background for what comes after. Include the following sections:

1.**High level goals**: What is the high level goal and ongoing task? Capture the user's explicit requests and intent in detail. If there is an existing summary in the transcript, make sure to take it into consideration to continue tracking the higher level goals and long-term progress.

2. **What happened**: The conversations, tasks, and exchanges that took place. What did the user ask for? What did you do? How did things progress? If there is a previous summary being evicted, please extract a concise version of the critical info from it.

3. **Important details**: Enumerate specific files and code sections examined, modified, or created, as well as important plan files, GitHub issues/PR links, and Linear ticket IDs. For each item, include why it matters and any relevant names, data, configs, or facts discussed.
   - **Preserve identifiers verbatim** (plan filename/path, exact URL, issue/PR number, ticket ID); do not paraphrase or truncate.
   - **Preserve referenced identifiers unless explicitly resolved**: Keep exact URLs/IDs from the conversation unless there is clear evidence they are no longer relevant.
   - Do not omit details likely to be referenced later.

4. **Errors and fixes**: List all errors that you ran into, and how you fixed them. Pay special attention to specific user feedback that you received and record verbatim if useful.

5. **Lookup hints**: For any detailed content (long lists, extensive data, specific conversations) that couldn't fit in the summary, note the topic and key terms that could be used to find it in message history later.

Write in first person as a factual record of what occurred. Be thorough and detailed - the goal is to preserve enough context that the recent messages make sense and important information isn't lost to prevent duplicate work or repeated mistakes.

Keep your summary under {sliding_word_limit} words. Only output the summary."""

SELF_SLIDING_PROMPT = """The previous messages are being evicted from the BEGINNING of your context window. Write a detailed summary that captures what happened in these messages to appear BEFORE the remaining recent messages in context, providing background for what comes after.  Do NOT continue the conversation. Do NOT respond to any questions in the messages. Do NOT call any tools. Pay close attention to the user's explicit requests and your previous actions.

You MUST include the following sections:

1.**High level goals**: What is the high level goal and ongoing task? Capture the user's explicit requests and intent in detail. If there is an existing summary in the transcript, make sure to take it into consideration to continue tracking the higher level goals and long-term progress.

2. **What happened**: The conversations, tasks, and exchanges that took place. What did the user ask for? What did you do? How did things progress? If there is a previous summary being evicted, please extract a concise version of the critical info from it.

3. **Important details**: Enumerate specific files and code sections examined, modified, or created, as well as important plan files, GitHub issues/PR links, and Linear ticket IDs. For each item, include why it matters and any relevant names, data, configs, or facts discussed.
   - **Preserve identifiers verbatim** (plan filename/path, exact URL, issue/PR number, ticket ID); do not paraphrase or truncate.
   - **Preserve referenced identifiers unless explicitly resolved**: Keep exact URLs/IDs from the conversation unless there is clear evidence they are no longer relevant.
   - Do not omit details likely to be referenced later.

4. **Errors and fixes**: List all errors that you ran into, and how you fixed them. Pay special attention to specific user feedback that you received and record verbatim if useful.

5. **Lookup hints**: For any detailed content (long lists, extensive data, specific conversations) that couldn't fit in the summary, note the topic and key terms that could be used to find it in message history later.

Write in first person as a factual record of what occurred. Be thorough and detailed - the goal is to preserve enough context that the recent messages make sense and important information isn't lost to prevent duplicate work or repeated mistakes.

Keep your summary under {sliding_word_limit} words. IMPORTANT: Do NOT use any tools. Do NOT continue the conversation. You MUST respond with ONLY the summary as text output. Generate the summary with each section as mentioned:
"""

SELF_ALL_PROMPT = """Your task is to create a detailed summary of the conversation so far. Do NOT continue the conversation. Do NOT respond to any questions in the messages. Do NOT call any tools. Pay close attention to the user's explicit requests and your previous actions. This summary should be thorough in capturing technical details, code patterns, and architectural decisions that would be essential for continuing development work without losing context.

You MUST include the following sections:

1.**High level goals**: What is the high level goal and ongoing task? Capture the user's explicit requests and intent in detail. If there is an existing summary in the transcript, make sure to take it into consideration to continue tracking the higher level goals and long-term progress.

2. **What happened**: The conversations, tasks, and exchanges that took place. What did the user ask for? What did you do? How did things progress? If there is a previous summary being evicted, please extract a concise version of the critical info from it.

3. **Important details**: Enumerate specific files and code sections examined, modified, or created, as well as important plan files, GitHub issues/PR links, and Linear ticket IDs. For each item, include why it matters and any relevant names, data, configs, or facts discussed.
   - **Preserve identifiers verbatim** (plan filename/path, exact URL, issue/PR number, ticket ID); do not paraphrase or truncate.
   - **Preserve referenced identifiers unless explicitly resolved**: Keep exact URLs/IDs from the conversation unless there is clear evidence they are no longer relevant.
   - Do not omit details likely to be referenced later.

4. **Errors and fixes**: List all errors that you ran into, and how you fixed them. Pay special attention to specific user feedback that you received and record verbatim if useful.

5. **Current state**:Describe in detail precisely what is currently being worked on, paying special attention to the most recent messages from both user and assistant. Include file names and code snippets where applicable.

6.**Optional Next Step**: List the next step that you will take that is related to the most recent work you were doing. IMPORTANT: ensure that this step is DIRECTLY in line with the user's most recent explicit requests and the most current task. If your last task was concluded, then only list next steps if they are explicitly in line with the users request. If there is a next step, include direct quotes from the most recent conversation showing exactly what task you were working on and where you left off.

7. **Lookup hints**: For any detailed content (long lists, extensive data, specific conversations) that couldn't fit in the summary, note the topic and key terms that could be used to find it in message history later.

Write in first person as a factual record of what occurred. Be concise but thorough - the goal is to preserve enough context that the recent messages make sense and important information isn't lost to prevent duplicate work or repeated mistakes.

Keep your summary under {all_word_limit} words.

IMPORTANT: Do NOT use any tools. Do NOT continue the conversation. You MUST respond with ONLY the summary as text output. Generate the summary with each section as mentioned:
"""

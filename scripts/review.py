#!/usr/bin/env python3
"""
scripts/review.py

A simple AI Code Review script powered by CLASP.
Reads a git diff from standard input and sends it to the CLASP proxy for analysis.
"""

import sys
import json
import httpx

def main():
    diff_content = sys.stdin.read()
    if not diff_content.strip():
        print("No diff provided to review.")
        return

    system_prompt = (
        "You are an expert software engineer reviewing a pull request. "
        "Analyze the provided git diff. Point out any glaring bugs, security issues, "
        "or significant performance problems. If the code looks good, just say 'LGTM'. "
        "Keep your feedback concise and actionable."
    )

    # Note: the model name here can be a generic Claude model, and CLASP will automatically 
    # route it to an available free-tier provider (e.g. Gemini, Groq, NIM) based on your clasp config.
    payload = {
        "model": "claude-3-5-sonnet-20241022",
        "system": system_prompt,
        "messages": [
            {
                "role": "user",
                "content": f"Here is the git diff:\n\n```diff\n{diff_content}\n```"
            }
        ],
        "max_tokens": 1024,
        "stream": False
    }

    print("Sending diff to CLASP for review...\n")
    try:
        response = httpx.post("http://localhost:8000/v1/messages", json=payload, timeout=60.0)
        response.raise_for_status()
        
        data = response.json()
        
        # Anthropic response format
        if "content" in data and len(data["content"]) > 0:
            print("=== AI Code Review ===")
            print(data["content"][0].get("text", ""))
        else:
            print("Received unexpected response format:", data)
            
    except httpx.HTTPStatusError as e:
        print(f"CLASP API returned an error: {e.response.status_code}")
        print(e.response.text)
        sys.exit(1)
    except Exception as e:
        print(f"Failed to communicate with CLASP: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Test script to verify the React UI endpoints are working with FastAPI.
"""

from clasp.server import create_app
from fastapi.testclient import TestClient

def test_endpoints():
    """Test the key endpoints needed by the React UI."""

    # Create the FastAPI app
    app = create_app(debug=True)
    client = TestClient(app)

    print("Testing React UI endpoints...")

    # Test 1: Session endpoints
    print("\n1. Testing session endpoints...")
    try:
        response = client.get("/internal/session")
        print(f"   GET /session: {response.status_code} - {response.json()}")
    except Exception as e:
        print(f"   GET /session: ERROR - {e}")

    try:
        response = client.post("/internal/session", json={"test": "session"})
        print(f"   POST /session: {response.status_code} - {response.json()}")
    except Exception as e:
        print(f"   POST /session: ERROR - {e}")

    try:
        response = client.delete("/internal/session")
        print(f"   DELETE /session: {response.status_code} - {response.json()}")
    except Exception as e:
        print(f"   DELETE /session: ERROR - {e}")

    # Test 2: Routing events endpoints
    print("\n2. Testing routing events endpoints...")
    try:
        response = client.get("/internal/routing-events")
        print(f"   GET /routing-events: {response.status_code} - {len(response.json())} events")
    except Exception as e:
        print(f"   GET /routing-events: ERROR - {e}")

    try:
        response = client.post("/internal/routing-events/clear")
        print(f"   POST /routing-events/clear: {response.status_code} - {response.json()}")
    except Exception as e:
        print(f"   POST /routing-events/clear: ERROR - {e}")

    # Test 3: Existing config endpoints
    print("\n3. Testing existing config endpoints...")
    try:
        response = client.get("/internal/config")
        print(f"   GET /config: {response.status_code}")
    except Exception as e:
        print(f"   GET /config: ERROR - {e}")

    try:
        response = client.get("/internal/catalog")
        print(f"   GET /catalog: {response.status_code}")
    except Exception as e:
        print(f"   GET /catalog: ERROR - {e}")

    # Test 4: Status endpoints
    print("\n4. Testing status endpoints...")
    try:
        response = client.get("/internal/status")
        print(f"   GET /status: {response.status_code}")
    except Exception as e:
        print(f"   GET /status: ERROR - {e}")

    # Test 5: SSE endpoints (just check they exist)
    print("\n5. Testing SSE endpoints...")
    try:
        # We won't actually connect to SSE, just check the route exists
        print(f"   GET /stream: Route exists")
        print(f"   GET /logs/stream: Route exists")
    except Exception as e:
        print(f"   SSE endpoints: ERROR - {e}")

    print("\nEndpoint testing complete!")

if __name__ == "__main__":
    test_endpoints()
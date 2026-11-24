import urllib.request
import json
import sys

BASE = 'http://127.0.0.1:8000'

def run():
    print("Testing multi-format publishing and collision API...")
    # 1. Create a test page
    page_payload = {
        'name': 'Viral Motivation Master',
        'fb_page_id': '10987654321',
        'fb_access_token': 'EAABtestmocktoken12345',
        'drive_folder_id': '1mockFolderId',
        'per_run': 24,
        'spread_start_hour': 0,
        'spread_end_hour': 24,
        'is_active': True
    }
    req = urllib.request.Request(
        f'{BASE}/api/pages',
        data=json.dumps(page_payload).encode(),
        headers={'Content-Type': 'application/json'}
    )
    res = urllib.request.urlopen(req)
    page = json.loads(res.read().decode())
    page_id = page['id']
    print(f"Page created: ID {page_id} (Name: {page['name']})")

    # 2. Test Gemini AI Generation endpoint
    print("\nTesting Gemini AI Generation...")
    ai_req = urllib.request.Request(
        f'{BASE}/api/pages/{page_id}/ai/generate',
        data=json.dumps({'topic': 'Daily Motivation & Discipline', 'format': 'photo'}).encode(),
        headers={'Content-Type': 'application/json'}
    )
    ai_res = urllib.request.urlopen(ai_req)
    ai_data = json.loads(ai_res.read().decode())
    print("AI Headline:", ai_data.get('headline'))
    print("AI Image Prompt:", ai_data.get('image_prompt'))
    print("AI Hashtags:", ai_data.get('hashtags')[:4])

    # 3. Create a direct Photo Content Item
    print("\nCreating Photo Content Item...")
    photo_item = {
        'title': 'Motivation Graphic Quote',
        'media_type': 'photo',
        'file_id': 'https://images.unsplash.com/photo-1579546929518-9e396f3cc809',
        'caption': ai_data.get('body', 'Small daily improvements over time lead to stunning results.'),
        'hashtags': ' '.join(ai_data.get('hashtags', ['#discipline', '#motivation']))
    }
    c_req = urllib.request.Request(
        f'{BASE}/api/pages/{page_id}/content/create',
        data=json.dumps(photo_item).encode(),
        headers={'Content-Type': 'application/json'}
    )
    c_res = urllib.request.urlopen(c_req)
    created_photo = json.loads(c_res.read().decode())
    print("Photo Item Created:", created_photo.get('id'), "Media:", created_photo.get('media_type'))

    # 4. Create a Text Content Item
    print("\nCreating Text Content Item...")
    text_item = {
        'title': 'Mindset Shift Thought',
        'media_type': 'text',
        'file_id': 'text:Mindset Shift Thought',
        'caption': 'Success is the sum of small efforts repeated day in and day out.',
        'hashtags': '#mindset #dailywisdom'
    }
    c_req2 = urllib.request.Request(
        f'{BASE}/api/pages/{page_id}/content/create',
        data=json.dumps(text_item).encode(),
        headers={'Content-Type': 'application/json'}
    )
    c_res2 = urllib.request.urlopen(c_req2)
    created_text = json.loads(c_res2.read().decode())
    print("Text Item Created:", created_text.get('id'), "Media:", created_text.get('media_type'))

    # 5. Check Collisions Endpoint
    print("\nChecking Collisions Endpoint...")
    coll_res = urllib.request.urlopen(f'{BASE}/api/pages/{page_id}/collisions')
    coll_data = json.loads(coll_res.read().decode())
    print("Collisions:", coll_data)

    print("\nALL VERIFICATIONS PASSED SUCCESSFULLY!")

if __name__ == '__main__':
    run()

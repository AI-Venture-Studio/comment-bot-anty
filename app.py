import requests
import dotenv

dotenv.load_dotenv()

# Login with token
api_url = 'http://localhost:3001/v1.0/auth/login-with-token'
token = dotenv.get_key(dotenv.find_dotenv(), 'DOLPHIN_API_TOKEN')

request_data = {
    'token': token
}

headers = {
    'Content-Type': 'application/json'
}

response = requests.post(api_url, json=request_data, headers=headers)

if response.status_code == 200:
    print('Successful login:', response.json())
    
    # API headers for public API
    api_headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json'
    }
    
    # Step 1: List ALL your browser profiles first to get valid IDs
    print('\n=== Fetching Your Browser Profiles ===')
    list_url = 'https://dolphin-anty-api.com/browser_profiles?limit=10'
    list_response = requests.get(list_url, headers=api_headers)
    
    if list_response.status_code == 200:
        profiles_data = list_response.json()
        profiles = profiles_data.get('data', [])
        total = profiles_data.get('total', 0)
        
        print(f'Total profiles found: {total}')
        
        if profiles:
            print('\nYour profiles:')
            for p in profiles:
                print(f"  - ID: {p.get('id')}, Name: {p.get('name')}, Platform: {p.get('platform')}")
            
            # Use the FIRST profile's ID
            profile_id = profiles[0].get('id')
            print(f'\n=== Using Profile ID: {profile_id} ===')
            
            # Step 2: Start the browser profile
            start_url = f'http://localhost:3001/v1.0/browser_profiles/{profile_id}/start?automation=1'
            
            start_response = requests.get(start_url, headers=headers)
            
            if start_response.status_code == 200:
                print(f'\n=== Profile {profile_id} Started ===')
                start_data = start_response.json()
                print(start_data)
                
                # Extract automation info
                if start_data.get('success'):
                    automation = start_data.get('automation', {})
                    print(f"\nWebSocket Endpoint: {automation.get('wsEndpoint')}")
                    print(f"Port: {automation.get('port')}")
            else:
                print(f'Error starting profile: {start_response.status_code}')
                print(start_response.text)
        else:
            print('\n⚠️  No browser profiles found!')
            print('Please create a browser profile in Dolphin{anty} first.')
    else:
        print(f'Error listing profiles: {list_response.status_code}')
        print(list_response.text)
else:
    print('Error:', response.status_code)
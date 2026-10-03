import re
import requests

# 1. Setup Base Configurations
BASE_DOMAIN = 'https://nextvnow.live'
MALAYALAM_CAT_URL = f'{BASE_DOMAIN}index.php?lan=3'

# Headers mimicking a standard browser request
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8'
}

def get_malayalam_channels():
    print("Connecting to service and fetching Malayalam channels...")
    
    try:
        # Establish a session to automatically handle required cookies
        session = requests.Session()
        session.headers.update(headers)
        
        # 1. Fetch the Malayalam Category Page
        response = session.get(MALAYALAM_CAT_URL, timeout=10)
        if response.status_code != 200:
            print(f"Failed to connect. Status Code: {response.status_code}")
            return

        html = response.text
        
        # 2. Parse Channel Titles and Link Paths 
        # (Finds content blocks and extracts the path inside href="...")
        items = re.findall(r'class="content"[^>]+href="([^"]+)".+?title">([^<]+)', html, re.DOTALL)
        
        if not items:
            print("No channels found. The site layout may have changed, or the domain is inactive.")
            return

        print(f"\nFound {len(items)} Malayalam Channels:\n" + "-"*50)
        
        # 3. Process each channel to get the final streaming URL
        for path, title in items:
            channel_page_url = BASE_DOMAIN + path.lstrip('/')
            
            # Request the individual channel page to find the play button link
            chan_response = session.get(channel_page_url, timeout=10)
            video_paths = re.findall(r'href="([^"]+)"\s*id="play"', chan_response.text)
            
            if video_paths:
                play_url = BASE_DOMAIN + video_paths[0].lstrip('/')
                
                # Update headers for the stream handshake
                session.headers.update({'X-Requested-With': 'com.jio.jioplay.livetv'})
                
                # Perform a request without following redirects to catch the 'Location' header
                stream_res = session.get(play_url, allow_redirects=False, timeout=10)
                final_stream = stream_res.headers.get('Location', 'No stream location found')
                
                print(f"Channel: {title.strip()}")
                print(f"Stream URL: {final_stream}")
                print("-" * 50)
            else:
                print(f"Channel: {title.strip()} -> (Could not extract player link)")

    except requests.exceptions.RequestException as e:
        print(f"An error occurred while connecting: {e}")

if __name__ == '__main__':
    get_malayalam_channels()
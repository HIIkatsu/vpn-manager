import re

with open('c:/Users/HIIkatsu/Desktop/vpn/bootstrap.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Change the connect button from <a> to <button>
old_btn = '''                <a id="connect_btn" href="#" class="btn w-full mt-4 bg-brand-500 hover:bg-brand-600 text-white font-semibold py-4 px-6 rounded-2xl flex items-center justify-center gap-3 transition-all duration-300">
                    <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"></path></svg>
                    Подключить VPN
                </a>'''

new_btn = '''                <button id="connect_btn" class="btn w-full mt-4 bg-brand-500 hover:bg-brand-600 text-white font-semibold py-4 px-6 rounded-2xl flex items-center justify-center gap-3 transition-all duration-300">
                    <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"></path></svg>
                    Подключить VPN
                </button>'''
content = content.replace(old_btn, new_btn)

# Replace updateConnectLink logic
old_logic = '''        function updateConnectLink() {
            const connectBtn = document.getElementById('connect_btn');
            const baseLink = document.getElementById("vless_link_val").value;
            connectBtn.href = baseLink;
        }'''

new_logic = '''        function updateConnectLink() {
            // Nothing to do here since we handle it in click listener
        }'''
content = content.replace(old_logic, new_logic)

# Add event listener logic inside DOMContentLoaded or similar
old_script_start = '''    <script>
        let currentStep = 1;'''

new_script_start = '''    <script>
        let currentStep = 1;
        document.addEventListener('DOMContentLoaded', () => {
            const connectBtn = document.getElementById('connect_btn');
            if(connectBtn) {
                connectBtn.addEventListener('click', function(e) {
                    e.preventDefault();
                    if (!selectedApp) return;
                    
                    var subUrl = "https://neurosmmai.ru/webhook/sub/tg_start";
                    var target = "";
                    if (selectedApp === 'hiddify') {
                        target = 'hiddify://install-config?url=' + encodeURIComponent(subUrl);
                    } else if (selectedApp === 'happ' || selectedApp === 'happ_ios') {
                        target = 'happ://add/' + subUrl;
                    } else if (selectedApp === 'v2raytun') {
                        target = 'v2raytun://import-remote-profile?url=' + encodeURIComponent(subUrl);
                    }
                    
                    try {
                        var a = document.createElement('a');
                        a.href = target;
                        a.rel = 'noopener';
                        a.style.display = 'none';
                        document.body.appendChild(a);
                        a.click();
                        setTimeout(function() { if (a.parentNode) a.parentNode.removeChild(a); }, 800);
                    } catch (err) {}
                    try {
                        var iframe = document.createElement('iframe');
                        iframe.style.cssText = 'display:none;width:0;height:0;border:0';
                        iframe.src = target;
                        document.body.appendChild(iframe);
                        setTimeout(function() { if (iframe.parentNode) iframe.parentNode.removeChild(iframe); }, 2500);
                    } catch (e) {}
                    try { window.location.replace(target); } catch (err) {
                        try { window.location.href = target; } catch (e2) {}
                    }
                });
            }
        });'''
content = content.replace(old_script_start, new_script_start)

with open('c:/Users/HIIkatsu/Desktop/vpn/bootstrap.html', 'w', encoding='utf-8') as f:
    f.write(content)

print("Updated bootstrap.html")

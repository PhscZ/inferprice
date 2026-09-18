import os
import re
import asyncio
from playwright.async_api import async_playwright

def load_model_filter(filepath='.env'):
    """
    Reads a plain-text list of model names from the specified file.
    Returns an ORDERED list of model names if the file exists and has content, otherwise None.
    Skips empty lines and lines starting with '#'.
    """
    if not os.path.exists(filepath):
        return None

    with open(filepath, 'r', encoding='utf-8') as f:
        models = []
        for line in f:
            stripped = line.strip()
            if stripped and not stripped.startswith('#'):
                models.append(stripped)

    return models if models else None

def normalize_data(data):
    """
    Normalizes the string values for numerical alignment:
    - Prices: Right-aligns BOTH numbers so decimals match.
    - Providers: Right-aligns ONLY the first number (min). The second number follows immediately.
    Format: "min - max"
    """
    # 1. Determine max decimal places for prices
    max_decimals = 0
    for m in data:
        for key in ['inputRange', 'outputRange']:
            val = m[key]
            if val in ['N/A', 'Not Found', '-']:
                continue
            nums = re.findall(r'[\d,]+(?:\.\d+)?', val)
            for n in nums:
                n_clean = n.replace(',', '')
                if '.' in n_clean:
                    decimals = len(n_clean.split('.')[1])
                    if decimals > max_decimals:
                        max_decimals = decimals

    # 2. Parse all values to calculate widths
    parsed_data = []
    for m in data:
        row = {}

        def get_nums(val, is_price):
            if val in ['N/A', 'Not Found', '-']:
                return []
            if is_price:
                return re.findall(r'[\d,.]+', val)
            else:
                return re.findall(r'\d+', val)

        in_p_nums = get_nums(m['inputRange'], True)
        out_p_nums = get_nums(m['outputRange'], True)
        in_a_nums = get_nums(m['inputAvail'], False)
        out_a_nums = get_nums(m['outputAvail'], False)

        row['in_p'] = [float(n.replace(',', '')) for n in in_p_nums]
        row['out_p'] = [float(n.replace(',', '')) for n in out_p_nums]
        row['in_a'] = [int(n) for n in in_a_nums]
        row['out_a'] = [int(n) for n in out_a_nums]

        parsed_data.append(row)

    # 3. Calculate widths for alignment
    max_price_width = 0
    max_prov_min_width = 0

    for row in parsed_data:
        # Prices (Align both numbers)
        for nums in [row['in_p'], row['out_p']]:
            if len(nums) >= 2:
                s1 = f"{nums[0]:.{max_decimals}f}"
                s2 = f"{nums[1]:.{max_decimals}f}"
                max_price_width = max(max_price_width, len(s1), len(s2))
            elif len(nums) == 1:
                s1 = f"{nums[0]:.{max_decimals}f}"
                max_price_width = max(max_price_width, len(s1))

        # Providers (Align ONLY the first number)
        for nums in [row['in_a'], row['out_a']]:
            if len(nums) >= 1:
                max_prov_min_width = max(max_prov_min_width, len(str(nums[0])))

    # 4. Format strings with alignment
    for i, row in enumerate(parsed_data):
        m = data[i]

        # Helper to format price range
        def format_price(nums):
            if not nums: return 'Not Found'
            if len(nums) >= 2:
                p1 = f"{nums[0]:.{max_decimals}f}".rjust(max_price_width)
                p2 = f"{nums[1]:.{max_decimals}f}".rjust(max_price_width)
                return f"${p1} - ${p2}"
            else:
                p1 = f"{nums[0]:.{max_decimals}f}".rjust(max_price_width)
                return f"${p1}"

        # Helper to format provider range
        def format_prov(nums):
            if not nums: return '-'
            if len(nums) >= 2:
                # Right-align the first number, keep the second number as-is
                v1 = str(nums[0]).rjust(max_prov_min_width)
                v2 = str(nums[1])
                return f"{v1} - {v2}"
            else:
                v1 = str(nums[0]).rjust(max_prov_min_width)
                return f"{v1}"

        m['inputRange'] = format_price(row['in_p'])
        m['outputRange'] = format_price(row['out_p'])
        m['inputAvail'] = format_prov(row['in_a'])
        m['outputAvail'] = format_prov(row['out_a'])

    return data

async def get_inferhub_price_ranges():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        print("Loading InferHub pricing page (waiting for JavaScript to render all tables)...")
        await page.goto('https://inferhub.dev/pricing')

        await page.wait_for_selector('table')
        await page.wait_for_timeout(3000)

        data = await page.evaluate('''() => {
            const results = [];
            const tables = document.querySelectorAll('table');

            tables.forEach(table => {
                const rows = Array.from(table.querySelectorAll('tr')).slice(1);

                rows.forEach(row => {
                    const cols = row.querySelectorAll('td');
                    if (cols.length >= 3) {
                        let modelId = "";
                        const modelEl = cols[0].querySelector('code') || cols[0].querySelector('.font-mono');
                        if (modelEl) {
                            const clone = modelEl.cloneNode(true);
                            clone.querySelectorAll('span').forEach(s => s.remove());
                            modelId = clone.innerText.split('\\n')[0].trim();
                        } else {
                            modelId = cols[0].innerText.split('\\n')[0].trim();
                        }

                        const extractTop2 = (cell) => {
                            const priceRows = Array.from(cell.querySelectorAll('div')).filter(div => div.querySelector('.min-w-14'));
                            let prices = [];
                            let avails = [];
                            const topRows = priceRows.slice(0, 2);

                            topRows.forEach(rowEl => {
                                const text = rowEl.innerText;
                                const priceMatch = text.match(/\\$[\\d,.]+/);
                                const availMatch = text.match(/(\\d+)\\s*avail/i);
                                if (priceMatch) prices.push(priceMatch[0]);
                                if (availMatch) avails.push(parseInt(availMatch[1], 10));
                            });

                            if (prices.length === 0) return { range: "N/A", avail: "0" };

                            const minPrice = prices[0];
                            const maxPrice = prices[prices.length - 1];
                            const priceRange = `${minPrice} - ${maxPrice}`;

                            const minAvail = avails[0] || 0;
                            const totalAvail = avails.reduce((a, b) => a + b, 0);
                            const availRange = `${minAvail} - ${totalAvail}`;

                            return { range: priceRange, avail: availRange };
                        };

                        const input = extractTop2(cols[1]);
                        const output = extractTop2(cols[2]);

                        results.push({
                            modelId: modelId,
                            inputRange: input.range,
                            inputAvail: input.avail,
                            outputRange: output.range,
                            outputAvail: output.avail
                        });
                    }
                });
            });
            return results;
        }''')

        await browser.close()

        # --- Apply .env Filter (preserving .env order) ---
        filter_models = load_model_filter()
        if filter_models:
            print(f"Filtering by {len(filter_models)} models from .env (displaying in defined order)...")
            data_lookup = {m['modelId']: m for m in data}
            ordered_data = []
            for model_name in filter_models:
                if model_name in data_lookup:
                    ordered_data.append(data_lookup[model_name])
                else:
                    ordered_data.append({
                        'modelId': model_name,
                        'inputRange': 'Not Found',
                        'inputAvail': '-',
                        'outputRange': 'Not Found',
                        'outputAvail': '-'
                    })
            data = ordered_data
        else:
            def parse_price_range(range_str):
                try:
                    match = re.search(r'[\d,.]+', range_str)
                    if match:
                        return float(match.group(0).replace(',', ''))
                    return float('inf')
                except:
                    return float('inf')
            data.sort(key=lambda x: parse_price_range(x['outputRange']))
        # --------------------------------------------------

        # NORMALIZE THE DATA STRINGS (Alignment)
        data = normalize_data(data)

        # --- DYNAMIC COLUMN WIDTH CALCULATION ---
        w_model = max((len(m['modelId']) for m in data), default=8)
        w_ip = max((len(m['inputRange']) for m in data), default=11)
        w_ia = max((len(m['inputAvail']) for m in data), default=11)
        w_op = max((len(m['outputRange']) for m in data), default=12)
        w_oa = max((len(m['outputAvail']) for m in data), default=12)

        # Set padding to 0 to rely on the " | " separator for spacing
        pad = 0
        w_model += pad
        w_ip += pad
        w_ia += pad
        w_op += pad
        w_oa += pad

        header = f"{'Model ID':<{w_model}} | {'Input Price':<{w_ip}} | {'Input Prov.':<{w_ia}} | {'Output Price':<{w_op}} | {'Output Prov.':<{w_oa}} |"
        print(header)
        print("-" * len(header))

        for m in data:
            print(f"{m['modelId']:<{w_model}} | {m['inputRange']:<{w_ip}} | {m['inputAvail']:<{w_ia}} | {m['outputRange']:<{w_op}} | {m['outputAvail']:<{w_oa}} |")

if __name__ == "__main__":
    asyncio.run(get_inferhub_price_ranges())

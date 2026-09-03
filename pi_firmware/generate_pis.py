# generate_pis.py
import hashlib, uuid

print("-- COPY AND PASTE THIS SQL INTO NEONDB --")
print()
for i in range(1, 11):
    dev_id = str(uuid.uuid4())
    raw_key = str(uuid.uuid4())
    hash_key = hashlib.sha256(raw_key.encode()).hexdigest()
    
    print(f"INSERT INTO pi_devices (id, society_id, name, api_key_hash, status) VALUES ('{dev_id}', 1, 'Pi {i}', '{hash_key}', 'active');")
    print(f"-- Pi {i} Config for Simulator -> ID: {dev_id} | KEY: {raw_key}")
    print("-" * 80)
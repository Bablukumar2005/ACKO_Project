from pulse import db, store, seed
db.reset()
db.connect(':memory:')
seed.seed_if_empty()
print('services:', [s['name'] for s in store.list_services()])
print('PROBE1 OK')

/*
 * wallcull: server-side anti-wallhack for prbf2_l64ded (LD_PRELOAD).
 *
 * The server decides per client which objects to send through
 * ServerConnection::calculateObjectPriority(). A priority of 0 means
 * "out of scope": the ghost is not sent and is removed on the client.
 * This hook returns 0 for enemy soldiers on foot that the client cannot
 * see, so wallhack/ESP has nothing to read.
 *
 * Addresses are for one exact build (see EXPECTED_PROLOGUE). On any
 * mismatch the hook is not installed and the server runs unchanged.
 */
#define _GNU_SOURCE
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

/* --- prbf2_l64ded addresses (sha256 dc63f03a...f819) --- */
#define ADDR_CALC_PRIORITY   0x43c220UL /* ServerConnection::calculateObjectPriority */
#define ADDR_GET_ROOT_PARENT 0x69a1e0UL /* world::getRootParent(IObject const*) */
#define ADDR_OBJECT_MANAGER  0x112ee48UL /* world::objectManager */
#define ADDR_IID_ISOLDIER    0xc7ce20UL /* world::IID_ISoldier */
#define ADDR_PRED_VTABLE     0xb339b0UL /* ProjectileVsTargetPredicator vtable+0x10 */
#define ADDR_IID_IPCO        0xc7ce6cUL /* world::IID_IPlayerControlObject */
#define ADDR_IID_IWEAPON     0xc7cea0UL /* world::IID_IWeaponObject */
#define PROLOGUE_LEN 18

static const unsigned char EXPECTED_PROLOGUE[PROLOGUE_LEN] = {
    0x4c, 0x89, 0x64, 0x24, 0xe0,  /* mov %r12,-0x20(%rsp) */
    0x4c, 0x89, 0x6c, 0x24, 0xe8,  /* mov %r13,-0x18(%rsp) */
    0x49, 0x89, 0xcc,              /* mov %rcx,%r12 */
    0x4c, 0x89, 0x74, 0x24, 0xf0,  /* mov %r14,-0x10(%rsp) */
};

/* vtable offsets */
#define VT_QUERY_INTERFACE 0x18   /* IObject::queryInterface(uint) */
#define VT_GET_POSITION    0xd8   /* Object::getAbsolutePosition() */
#define VT_SOLDIER_TEAM    0x3e0  /* Soldier::getTeam() */
#define VT_PLAYER_VEHICLE  0x90   /* Player::getVehicle() */
#define VT_PLAYER_TEAM     0x1d0  /* Player::getTeam() */
#define VT_INTERSECT_LINE  0xd0   /* ObjectManager::intersectLine(...) */
#define VT_PHYS_VELOCITY   0xc0   /* physics node at object+0x88 */
#define OBJ_PHYSICS        0x88
#define VT_PCO_WEAPON      0xe0   /* IPlayerControlObject: weapon in slot i */
#define VT_WEAPON_FIRING   0x38   /* IWeaponObject::isFiring() */
#define VT_WEAPON_NOISY    0x228  /* IWeaponObject::getNoisy(), 0 = suppressed */
#define WEAPON_SLOTS       3      /* as in Player::updateGhostFiringState */

typedef float (*calc_fn)(void *self, void *desc, float a, void *player,
                         float *mat, const float *vec, float b, float c, int d);
typedef void *(*root_fn)(void *obj);
typedef void *(*qi_fn)(void *obj, unsigned int iid);
typedef float *(*vec_fn)(void *obj);
typedef int (*int_fn)(void *obj);
typedef void *(*ptr_fn)(void *obj);
typedef void *(*slot_fn)(void *obj, int slot);
typedef char (*line_fn)(void *om, void **hit, float *pos, float *normal, int *mat,
                        const float *from, const float *delta, void *pred,
                        int b1, int b2, int b3, int b4, unsigned int mask);

struct predicator {          /* layout copied from GameLogic::findBestTargetObject */
    uintptr_t vtable;
    uint32_t flags;          /* 0x100 */
    uint32_t mode;           /* 8 */
    void *ignore_a;
    void *ignore_b;
};

#define VCALL(obj, off, type) ((type)(*(void ***)(obj))[(off) / sizeof(void *)])

/* --- settings: env at startup, then wallcull.cfg (reloaded live) --- */
static float near_dist = 20.0f;      /* always send inside this radius (footsteps) */
static float hold_time = 1.5f;       /* keep sending after last clear ray */
static float recheck = 0.2f;         /* seconds between checks per pair */
static float lead_time = 0.25f;      /* predict target movement (ping + re-ghost) */
static float ray_budget = 40000;     /* rays per second; over budget = visible */
static float log_interval = 60;      /* seconds between stats lines, 0 = off */
static float shot_range = 150.0f;    /* send a firing enemy within this radius */
static float quiet_range = 40.0f;    /* same, for suppressed weapons */
static float shot_hold = 1.5f;       /* keep sending after the last shot */

struct setting {
    const char *key;     /* key in wallcull.cfg */
    const char *env;     /* environment variable */
    float *value;
    float min, max;
};

static const struct setting settings[] = {
    { "near",        "WALLCULL_NEAR",        &near_dist,    0, 500 },
    { "hold",        "WALLCULL_HOLD",        &hold_time,    0, 10 },
    { "recheck",     "WALLCULL_RECHECK",     &recheck,      0.02f, 5 },
    { "lead",        "WALLCULL_LEAD",        &lead_time,    0, 2 },
    { "ray_budget",  "WALLCULL_RAY_BUDGET",  &ray_budget,   0, 1e7f },
    { "log",         "WALLCULL_LOG",         &log_interval, 0, 86400 },
    { "shot_range",  "WALLCULL_SHOT_RANGE",  &shot_range,   0, 5000 },
    { "quiet_range", "WALLCULL_QUIET_RANGE", &quiet_range,  0, 5000 },
    { "shot_hold",   "WALLCULL_SHOT_HOLD",   &shot_hold,    0, 30 },
};
#define NUM_SETTINGS (sizeof(settings) / sizeof(settings[0]))

static const char *config_path = "wallcull.cfg";
static time_t config_mtime;
static double config_checked;

static calc_fn orig_calc;
static long rays_this_sec, rays_total, culled_total, checks_total, over_budget, shot_total;
static double sec_start, last_log;

static double now_sec(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec * 1e-9;
}

/* --- visibility cache: (viewer, target) -> state --- */
#define CACHE_SIZE 65536
struct entry {
    void *viewer;
    void *target;
    double next_check;
    double visible_until;
};
static struct entry cache[CACHE_SIZE];

static struct entry *lookup(void *viewer, void *target, double now)
{
    uintptr_t h = ((uintptr_t)viewer * 2654435761u) ^ ((uintptr_t)target >> 4);
    for (int i = 0; i < 8; i++) {
        struct entry *e = &cache[(h + i) & (CACHE_SIZE - 1)];
        if (e->viewer == viewer && e->target == target)
            return e;
        if (!e->viewer || e->next_check + 10.0 < now) {
            e->viewer = viewer;
            e->target = target;
            e->next_check = 0;
            e->visible_until = 0;
            return e;
        }
    }
    return NULL;
}

/* --- last shot per soldier --- */
#define SHOT_SIZE 1024
struct shot {
    void *soldier;
    double checked;
    double last_shot;
    int noisy;
};
static struct shot shots[SHOT_SIZE];

/* Weapon lookup mirrors Player::updateGhostFiringState. */
static void sample_firing(void *root, struct shot *s)
{
    void *pco = VCALL(root, VT_QUERY_INTERFACE, qi_fn)(root, *(unsigned int *)ADDR_IID_IPCO);
    if (!pco)
        return;
    unsigned int iid = *(unsigned int *)ADDR_IID_IWEAPON;
    for (int i = 0; i < WEAPON_SLOTS; i++) {
        void *w = VCALL(pco, VT_PCO_WEAPON, slot_fn)(pco, i);
        if (!w || (*((unsigned char *)w + 8) & 1))
            continue;
        void *iw = VCALL(w, VT_QUERY_INTERFACE, qi_fn)(w, iid);
        if (!iw || !(VCALL(iw, VT_WEAPON_FIRING, int_fn)(iw) & 0xff))
            continue;
        s->last_shot = s->checked;
        s->noisy = VCALL(iw, VT_WEAPON_NOISY, int_fn)(iw) & 0xff;
        shot_total++;
        return;
    }
}

/* Heard by the viewer: fired within shot_hold and inside hearing range. */
static int heard_shot(void *root, float dist2, double now)
{
    uintptr_t h = ((uintptr_t)root >> 4) * 2654435761u;
    struct shot *s = NULL;
    for (int i = 0; i < 4; i++) {
        struct shot *c = &shots[(h + i) & (SHOT_SIZE - 1)];
        if (c->soldier == root) {
            s = c;
            break;
        }
        if (!s && (!c->soldier || c->checked + 10.0 < now))
            s = c;
    }
    if (!s)
        return 0;
    if (s->soldier != root) {
        s->soldier = root;
        s->last_shot = -1e9;
        s->noisy = 0;
        s->checked = 0;
    }
    if (now - s->checked >= 0.01) {     /* once per server frame */
        s->checked = now;
        sample_firing(root, s);
    }
    if (now - s->last_shot > shot_hold)
        return 0;
    float range = s->noisy ? shot_range : quiet_range;
    return dist2 <= range * range;
}

static int ray_clear(void *om, const float *from, const float *to,
                     void *viewer_root, void *target_root)
{
    struct predicator pred = { ADDR_PRED_VTABLE, 0x100, 8, target_root, viewer_root };
    float delta[3] = { to[0] - from[0], to[1] - from[1], to[2] - from[2] };
    float pos[3], normal[3];
    void *hit = NULL;
    int material = 0;

    rays_this_sec++;
    rays_total++;
    return !VCALL(om, VT_INTERSECT_LINE, line_fn)(om, &hit, pos, normal, &material,
                                                  from, delta, &pred, 1, 0, 0, 1, 0);
}

static int can_see(const float *eye_mat, void *viewer_root, void *target_root,
                   const float *tpos)
{
    void *om = *(void **)ADDR_OBJECT_MANAGER;
    if (!om)
        return 1;

    float eyes[2][3] = {
        { eye_mat[12], eye_mat[13], eye_mat[14] },
        { eye_mat[12], eye_mat[13] + 0.5f, eye_mat[14] },   /* peeking over cover */
    };

    float vel[3] = { 0, 0, 0 };
    void *phys = *(void **)((char *)target_root + OBJ_PHYSICS);
    if (phys) {
        float *v = VCALL(phys, VT_PHYS_VELOCITY, vec_fn)(phys);
        if (v) {
            vel[0] = v[0];
            vel[1] = v[1];
            vel[2] = v[2];
        }
    }

    static const float heights[3] = { 1.6f, 0.9f, 0.3f };  /* head, chest, prone */
    for (int p = 0; p < 2; p++) {
        float base[3] = {
            tpos[0] + vel[0] * lead_time * p,
            tpos[1] + vel[1] * lead_time * p,
            tpos[2] + vel[2] * lead_time * p,
        };
        for (int h = 0; h < 3; h++) {
            float point[3] = { base[0], base[1] + heights[h], base[2] };
            for (int e = 0; e < 2; e++) {
                if (rays_this_sec >= ray_budget) {
                    over_budget++;
                    return 1;
                }
                if (ray_clear(om, eyes[e], point, viewer_root, target_root))
                    return 1;
            }
        }
    }
    return 0;
}

static void print_settings(const char *what)
{
    fprintf(stderr, "wallcull: %s", what);
    for (size_t i = 0; i < NUM_SETTINGS; i++)
        fprintf(stderr, " %s=%g", settings[i].key, *settings[i].value);
    fprintf(stderr, "\n");
}

static int set_value(const struct setting *s, const char *text, const char *source)
{
    char *end;
    float v = strtof(text, &end);
    while (*end == ' ' || *end == '\t' || *end == '\r' || *end == '\n')
        end++;
    if (end == text || *end || v < s->min || v > s->max) {
        fprintf(stderr, "wallcull: %s: bad %s '%s' (allowed %g..%g), kept %g\n",
                source, s->key, text, s->min, s->max, *s->value);
        return 0;
    }
    *s->value = v;
    return 1;
}

/* Lines "key = value", '#' starts a comment. Unknown keys are reported. */
static void load_config(void)
{
    FILE *f = fopen(config_path, "r");
    if (!f)
        return;
    char line[256];
    int lineno = 0;
    while (fgets(line, sizeof(line), f)) {
        lineno++;
        char *hash = strchr(line, '#');
        if (hash)
            *hash = 0;
        char *eq = strchr(line, '=');
        char *k = line;
        while (*k == ' ' || *k == '\t')
            k++;
        if (!*k || *k == '\n' || *k == '\r')
            continue;
        if (!eq) {
            fprintf(stderr, "wallcull: %s:%d: expected key = value\n", config_path, lineno);
            continue;
        }
        char *kend = eq;
        while (kend > k && (kend[-1] == ' ' || kend[-1] == '\t'))
            kend--;
        *kend = 0;
        char *v = eq + 1;
        while (*v == ' ' || *v == '\t')
            v++;
        char *vend = v + strlen(v);
        while (vend > v && (vend[-1] == ' ' || vend[-1] == '\t' || vend[-1] == '\r' || vend[-1] == '\n'))
            vend--;
        *vend = 0;
        size_t i;
        for (i = 0; i < NUM_SETTINGS; i++)
            if (!strcmp(k, settings[i].key))
                break;
        if (i == NUM_SETTINGS)
            fprintf(stderr, "wallcull: %s:%d: unknown key '%s'\n", config_path, lineno, k);
        else
            set_value(&settings[i], v, config_path);
    }
    fclose(f);
}

static void reload_config_if_changed(double now)
{
    if (now - config_checked < 5.0)
        return;
    config_checked = now;
    struct stat st;
    if (stat(config_path, &st) || st.st_mtime == config_mtime)
        return;
    config_mtime = st.st_mtime;
    load_config();
    print_settings("settings reloaded:");
}

static float hooked_calc(void *self, void *desc, float a, void *player,
                         float *mat, const float *vec, float b, float c, int d)
{
    float prio = orig_calc(self, desc, a, player, mat, vec, b, c, d);
    if (prio <= 0.0f || !desc || !player || !mat)
        return prio;

    double now = now_sec();
    reload_config_if_changed(now);
    if (now - sec_start >= 1.0) {
        sec_start = now;
        rays_this_sec = 0;
    }
    if (log_interval > 0 && now - last_log >= log_interval) {
        last_log = now;
        fprintf(stderr, "wallcull: checks=%ld rays=%ld culled=%ld over_budget=%ld shots=%ld\n",
                checks_total, rays_total, culled_total, over_budget, shot_total);
    }

    /* desc->0x10->[0]->[0]->0x28->0x10, same path as the original */
    void *p = *(void **)((char *)desc + 0x10);
    if (!p || !(p = *(void **)p) || !(p = *(void **)p))
        return prio;
    p = *(void **)((char *)p + 0x28);
    if (!p || !(p = *(void **)((char *)p + 0x10)))
        return prio;

    void *root = ((root_fn)ADDR_GET_ROOT_PARENT)(p);
    if (!root)
        return prio;
    unsigned int iid = *(unsigned int *)ADDR_IID_ISOLDIER;
    if (!VCALL(root, VT_QUERY_INTERFACE, qi_fn)(root, iid))
        return prio;    /* vehicles, items, projectiles: unchanged */

    int viewer_team = VCALL(player, VT_PLAYER_TEAM, int_fn)(player);
    if (viewer_team != 1 && viewer_team != 2)
        return prio;    /* spectators */
    if (VCALL(root, VT_SOLDIER_TEAM, int_fn)(root) == viewer_team)
        return prio;

    float *tpos = VCALL(root, VT_GET_POSITION, vec_fn)(root);
    if (!tpos)
        return prio;
    float dx = tpos[0] - mat[12], dy = tpos[1] - mat[13], dz = tpos[2] - mat[14];
    float dist2 = dx * dx + dy * dy + dz * dz;
    if (dist2 <= near_dist * near_dist)
        return prio;
    if (heard_shot(root, dist2, now))
        return prio;    /* audible gunfire: send so the client can play it */

    struct entry *e = lookup(player, root, now);
    if (!e)
        return prio;
    if (now >= e->next_check) {
        e->next_check = now + recheck;
        checks_total++;
        void *viewer_root = NULL;
        void *veh = VCALL(player, VT_PLAYER_VEHICLE, ptr_fn)(player);
        if (veh)
            viewer_root = ((root_fn)ADDR_GET_ROOT_PARENT)(veh);
        if (can_see(mat, viewer_root, root, tpos))
            e->visible_until = now + hold_time;
    }
    if (now < e->visible_until)
        return prio;

    culled_total++;
    return 0.0f;
}

static void *make_trampoline(unsigned char *target)
{
    unsigned char *t = mmap(NULL, 64, PROT_READ | PROT_WRITE | PROT_EXEC,
                            MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (t == MAP_FAILED)
        return NULL;
    memcpy(t, target, PROLOGUE_LEN);
    /* jmp *0(%rip); .quad target+PROLOGUE_LEN */
    unsigned char *j = t + PROLOGUE_LEN;
    j[0] = 0xff;
    j[1] = 0x25;
    memset(j + 2, 0, 4);
    uint64_t back = (uint64_t)(target + PROLOGUE_LEN);
    memcpy(j + 6, &back, 8);
    return t;
}

static int patch_jump(unsigned char *target, void *dest)
{
    long page = sysconf(_SC_PAGESIZE);
    uintptr_t start = (uintptr_t)target & ~(uintptr_t)(page - 1);
    if (mprotect((void *)start, page * 2, PROT_READ | PROT_WRITE | PROT_EXEC))
        return -1;
    unsigned char code[14] = { 0xff, 0x25, 0, 0, 0, 0 };
    uint64_t to = (uint64_t)dest;
    memcpy(code + 6, &to, 8);
    memcpy(target, code, sizeof(code));
    mprotect((void *)start, page * 2, PROT_READ | PROT_EXEC);
    return 0;
}

__attribute__((constructor)) static void wallcull_init(void)
{
    char exe[256] = { 0 };
    if (readlink("/proc/self/exe", exe, sizeof(exe) - 1) < 0 || !strstr(exe, "prbf2_l64ded"))
        return;
    if (getenv("WALLCULL_DISABLE")) {
        fprintf(stderr, "wallcull: disabled by WALLCULL_DISABLE\n");
        return;
    }

    for (size_t i = 0; i < NUM_SETTINGS; i++) {
        const char *v = getenv(settings[i].env);
        if (v && *v)
            set_value(&settings[i], v, "env");
    }
    const char *cfg = getenv("WALLCULL_CONFIG");
    if (cfg && *cfg)
        config_path = cfg;
    struct stat st;
    if (!stat(config_path, &st)) {
        config_mtime = st.st_mtime;
        load_config();
    }

    unsigned char *target = (unsigned char *)ADDR_CALC_PRIORITY;
    if (memcmp(target, EXPECTED_PROLOGUE, PROLOGUE_LEN)) {
        fprintf(stderr, "wallcull: unknown server build, hook not installed\n");
        return;
    }
    orig_calc = (calc_fn)make_trampoline(target);
    if (!orig_calc || patch_jump(target, (void *)hooked_calc)) {
        fprintf(stderr, "wallcull: patch failed, hook not installed\n");
        return;
    }
    fprintf(stderr, "wallcull: config %s\n", config_path);
    print_settings("active");
}

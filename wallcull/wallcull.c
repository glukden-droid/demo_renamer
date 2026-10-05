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
#include <time.h>
#include <unistd.h>

/* --- prbf2_l64ded addresses (sha256 dc63f03a...f819) --- */
#define ADDR_CALC_PRIORITY   0x43c220UL /* ServerConnection::calculateObjectPriority */
#define ADDR_GET_ROOT_PARENT 0x69a1e0UL /* world::getRootParent(IObject const*) */
#define ADDR_OBJECT_MANAGER  0x112ee48UL /* world::objectManager */
#define ADDR_IID_ISOLDIER    0xc7ce20UL /* world::IID_ISoldier */
#define ADDR_PRED_VTABLE     0xb339b0UL /* ProjectileVsTargetPredicator vtable+0x10 */
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

typedef float (*calc_fn)(void *self, void *desc, float a, void *player,
                         float *mat, const float *vec, float b, float c, int d);
typedef void *(*root_fn)(void *obj);
typedef void *(*qi_fn)(void *obj, unsigned int iid);
typedef float *(*vec_fn)(void *obj);
typedef int (*int_fn)(void *obj);
typedef void *(*ptr_fn)(void *obj);
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

/* --- settings (env) --- */
static float near_dist = 20.0f;      /* always send inside this radius (footsteps) */
static float hold_time = 1.5f;       /* keep sending after last clear ray */
static float recheck = 0.2f;         /* seconds between checks per pair */
static float lead_time = 0.25f;      /* predict target movement (ping + re-ghost) */
static long ray_budget = 40000;      /* rays per second; over budget = visible */
static int log_interval = 60;

static calc_fn orig_calc;
static long rays_this_sec, rays_total, culled_total, checks_total, over_budget;
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

static float hooked_calc(void *self, void *desc, float a, void *player,
                         float *mat, const float *vec, float b, float c, int d)
{
    float prio = orig_calc(self, desc, a, player, mat, vec, b, c, d);
    if (prio <= 0.0f || !desc || !player || !mat)
        return prio;

    double now = now_sec();
    if (now - sec_start >= 1.0) {
        sec_start = now;
        rays_this_sec = 0;
    }
    if (log_interval > 0 && now - last_log >= log_interval) {
        last_log = now;
        fprintf(stderr, "wallcull: checks=%ld rays=%ld culled=%ld over_budget=%ld\n",
                checks_total, rays_total, culled_total, over_budget);
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
    if (dx * dx + dy * dy + dz * dz <= near_dist * near_dist)
        return prio;

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

static float env_float(const char *name, float def)
{
    const char *v = getenv(name);
    return v && *v ? (float)atof(v) : def;
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

    near_dist = env_float("WALLCULL_NEAR", near_dist);
    hold_time = env_float("WALLCULL_HOLD", hold_time);
    recheck = env_float("WALLCULL_RECHECK", recheck);
    lead_time = env_float("WALLCULL_LEAD", lead_time);
    ray_budget = (long)env_float("WALLCULL_RAY_BUDGET", (float)ray_budget);
    log_interval = (int)env_float("WALLCULL_LOG", (float)log_interval);

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
    fprintf(stderr, "wallcull: active near=%.0f hold=%.2f recheck=%.2f lead=%.2f budget=%ld\n",
            near_dist, hold_time, recheck, lead_time, ray_budget);
}

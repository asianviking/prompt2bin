#ifndef GAME_FRAME_SCRATCH_H
#define GAME_FRAME_SCRATCH_H

#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>

#define GAME_FRAME_SCRATCH_PAGE_SIZE     65536u
#define GAME_FRAME_SCRATCH_CAPACITY      65520u
#define GAME_FRAME_SCRATCH_MIN_ALIGN     8u

/* Header overhead: sizeof(game_frame_scratch_t) == 16 bytes.
   Backing buffer immediately follows in the same aligned_alloc block,
   giving a single contiguous 65536-byte allocation for cache locality. */
typedef struct {
    size_t   offset;     /* current bump pointer offset into the buffer */
    uint64_t generation; /* incremented on every reset for use-after-reset detection */
} game_frame_scratch_t;

/* Returns a pointer to the usable buffer region that trails the header. */
static inline uint8_t *
game_frame_scratch__buf(game_frame_scratch_t *arena)
{
    return (uint8_t *)arena + sizeof(game_frame_scratch_t);
}

/* Allocate and initialise a new arena.
   A single aligned_alloc call covers both the header struct and the 65520-byte
   backing buffer, keeping them on the same cache lines and avoiding a second
   pointer indirection at alloc time. */
static inline game_frame_scratch_t *
game_frame_scratch_create(void)
{
    game_frame_scratch_t *arena = (game_frame_scratch_t *)aligned_alloc(
        GAME_FRAME_SCRATCH_MIN_ALIGN,
        GAME_FRAME_SCRATCH_PAGE_SIZE
    );
    if (!arena) {
        return NULL;
    }
    arena->offset     = 0u;
    arena->generation = 0u;
    return arena;
}

/* Bump-allocate `size` bytes with GAME_FRAME_SCRATCH_MIN_ALIGN (8-byte) alignment.
   Returns NULL when the arena has insufficient space. */
static inline void *
game_frame_scratch_alloc(game_frame_scratch_t *arena, size_t size)
{
    if (!arena || size == 0u) {
        return NULL;
    }

    /* Align up the current offset to the minimum alignment boundary.
       Formula is guaranteed not to overflow when offset < CAPACITY and
       (align - 1) is a constant small value. */
    size_t aligned_offset =
        (arena->offset + (GAME_FRAME_SCRATCH_MIN_ALIGN - 1u)) &
        ~(GAME_FRAME_SCRATCH_MIN_ALIGN - 1u);

    /* Bounds check: reject if allocation would exceed usable capacity. */
    if (aligned_offset + size > GAME_FRAME_SCRATCH_CAPACITY) {
        return NULL;
    }

    void *ptr        = (void *)(game_frame_scratch__buf(arena) + aligned_offset);
    arena->offset    = aligned_offset + size;
    return ptr;
}

/* Reset the bump pointer to zero and advance the generation counter.
   Any pointer obtained before this call is logically invalid; callers
   can detect stale pointers by snapshotting the generation at alloc time
   and comparing after a potential reset. */
static inline void
game_frame_scratch_reset(game_frame_scratch_t *arena)
{
    if (!arena) {
        return;
    }
    arena->generation++;
    arena->offset = 0u;
}

/* Release all memory held by the arena (struct + backing buffer together). */
static inline void
game_frame_scratch_destroy(game_frame_scratch_t *arena)
{
    if (!arena) {
        return;
    }
    free(arena);
}

#endif /* GAME_FRAME_SCRATCH_H */
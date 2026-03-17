#ifndef CRYPTO_KEY_BUF_H
#define CRYPTO_KEY_BUF_H

#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

/* -----------------------------------------------------------------------
 * Verified spec constants
 *   page_size        = 4096
 *   header_overhead  = 16   (== sizeof(crypto_key_buf_t))
 *   usable_capacity  = 4080
 *   min_alignment    = 16
 *   max_pages        = 1
 *   zero_on_alloc    = true
 *   zero_on_reset    = true
 *   use_after_reset  = detected via generation counter
 * --------------------------------------------------------------------- */

#define CRYPTO_KEY_BUF_PAGE_SIZE  ((size_t)4096)
#define CRYPTO_KEY_BUF_HDR_SIZE   ((size_t)16)
#define CRYPTO_KEY_BUF_CAPACITY   ((size_t)4080)
#define CRYPTO_KEY_BUF_MIN_ALIGN  ((size_t)16)

/* Single-allocation layout (one aligned_alloc call, exactly one page):
 *
 *   [ crypto_key_buf_t header : 16 bytes ][ backing buffer : 4080 bytes ]
 *   ^--- base pointer (16-byte aligned via aligned_alloc)
 */
typedef struct {
    size_t   offset;      /* bump pointer: bytes consumed in backing buffer */
    uint64_t generation;  /* incremented on every reset for UAR detection   */
} crypto_key_buf_t;

/* Compile-time layout assertion: header must be exactly 16 bytes. */
typedef char crypto_key_buf__hdr_size_check[
    (sizeof(crypto_key_buf_t) == CRYPTO_KEY_BUF_HDR_SIZE) ? 1 : -1
];

/* -----------------------------------------------------------------------
 * crypto_key_buf_create
 *
 * Allocates exactly one page (4096 bytes) with 16-byte base alignment.
 * The struct occupies the first 16 bytes; the remaining 4080 bytes are
 * the backing buffer, zeroed before return.
 * --------------------------------------------------------------------- */
static inline crypto_key_buf_t *crypto_key_buf_create(void)
{
    crypto_key_buf_t *arena = (crypto_key_buf_t *)aligned_alloc(
        CRYPTO_KEY_BUF_MIN_ALIGN,
        CRYPTO_KEY_BUF_PAGE_SIZE
    );
    if (!arena) {
        return NULL;
    }

    arena->offset     = 0;
    arena->generation = 0;

    /* Zero the entire backing buffer on creation. */
    memset((unsigned char *)arena + CRYPTO_KEY_BUF_HDR_SIZE,
           0,
           CRYPTO_KEY_BUF_CAPACITY);

    return arena;
}

/* -----------------------------------------------------------------------
 * crypto_key_buf_alloc
 *
 * Bump-allocates `size` bytes from the arena with guaranteed 16-byte
 * alignment.  Returns NULL if the arena is full or arguments are invalid.
 *
 * Alignment formula (verified overflow-safe for size <= CAPACITY):
 *   aligned_offset = (offset + (align-1)) & ~(align-1)
 *
 * Bounds check:
 *   aligned_offset + size <= CRYPTO_KEY_BUF_CAPACITY
 *
 * The returned region is zeroed (zero_on_alloc).
 * --------------------------------------------------------------------- */
static inline void *crypto_key_buf_alloc(crypto_key_buf_t *arena, size_t size)
{
    if (!arena || size == 0) {
        return NULL;
    }

    /* Reject any single request that can never fit. */
    if (size > CRYPTO_KEY_BUF_CAPACITY) {
        return NULL;
    }

    /* Align the current offset up to the minimum alignment. */
    size_t aligned_offset =
        (arena->offset + (CRYPTO_KEY_BUF_MIN_ALIGN - 1u)) &
        ~(CRYPTO_KEY_BUF_MIN_ALIGN - 1u);

    /* Bounds check written to avoid addition overflow:
     *   aligned_offset + size <= CAPACITY
     *   <=>  size <= CAPACITY - aligned_offset  (safe since size <= CAPACITY) */
    if (aligned_offset > CRYPTO_KEY_BUF_CAPACITY ||
        size > CRYPTO_KEY_BUF_CAPACITY - aligned_offset)
    {
        return NULL;
    }

    /* Resolve pointer into the backing buffer that follows the header. */
    unsigned char *buf = (unsigned char *)arena + CRYPTO_KEY_BUF_HDR_SIZE;
    void          *ptr = buf + aligned_offset;

    /* Advance bump pointer. */
    arena->offset = aligned_offset + size;

    /* Zero on alloc: guaranteed clean memory for cryptographic material. */
    memset(ptr, 0, size);

    return ptr;
}

/* -----------------------------------------------------------------------
 * crypto_key_buf_reset
 *
 * Zeroes all previously allocated bytes (zero_on_reset) to wipe any
 * cryptographic material, then resets the bump pointer to 0.
 * The generation counter is incremented so callers can detect stale
 * pointers (use-after-reset detection).
 * --------------------------------------------------------------------- */
static inline void crypto_key_buf_reset(crypto_key_buf_t *arena)
{
    if (!arena) {
        return;
    }

    /* Zero on reset: scrub live allocation region before releasing it. */
    if (arena->offset > 0) {
        unsigned char *buf = (unsigned char *)arena + CRYPTO_KEY_BUF_HDR_SIZE;
        memset(buf, 0, arena->offset);
    }

    /* Increment generation to invalidate outstanding pointer snapshots. */
    arena->generation++;

    /* Reset bump pointer. */
    arena->offset = 0;
}

/* -----------------------------------------------------------------------
 * crypto_key_buf_destroy
 *
 * Scrubs the entire page (header + buffer) to erase any residual key
 * material before returning memory to the OS.
 * --------------------------------------------------------------------- */
static inline void crypto_key_buf_destroy(crypto_key_buf_t *arena)
{
    if (!arena) {
        return;
    }

    /* Defensive scrub of the full page before free. */
    memset((void *)arena, 0, CRYPTO_KEY_BUF_PAGE_SIZE);
    free((void *)arena);
}

#endif /* CRYPTO_KEY_BUF_H */
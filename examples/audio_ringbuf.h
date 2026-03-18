#ifndef AUDIO_RING_H
#define AUDIO_RING_H

#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdatomic.h>

#define AUDIO_RING_CAPACITY 4096
#define AUDIO_RING_ELEMENT_SIZE 4
#define AUDIO_RING_MASK (AUDIO_RING_CAPACITY - 1)
#define AUDIO_RING_BUFFER_SIZE (AUDIO_RING_CAPACITY * AUDIO_RING_ELEMENT_SIZE)
#define CACHE_LINE_SIZE 64

typedef struct {
    _Atomic(uint64_t) head;
    char _pad0[CACHE_LINE_SIZE - sizeof(_Atomic(uint64_t))];
    
    _Atomic(uint64_t) tail;
    char _pad1[CACHE_LINE_SIZE - sizeof(_Atomic(uint64_t))];
    
    uint8_t buffer[AUDIO_RING_BUFFER_SIZE];
} audio_ring_t;

static inline audio_ring_t *audio_ring_create(void) {
    audio_ring_t *rb = (audio_ring_t *)malloc(sizeof(audio_ring_t));
    if (!rb) return NULL;
    
    atomic_store_explicit(&rb->head, 0, memory_order_relaxed);
    atomic_store_explicit(&rb->tail, 0, memory_order_relaxed);
    memset(rb->buffer, 0, AUDIO_RING_BUFFER_SIZE);
    
    return rb;
}

static inline int audio_ring_push(audio_ring_t *rb, const void *data) {
    uint64_t head = atomic_load_explicit(&rb->head, memory_order_relaxed);
    uint64_t tail = atomic_load_explicit(&rb->tail, memory_order_relaxed);
    
    if (head - tail >= AUDIO_RING_CAPACITY) {
        return -1;
    }
    
    uint32_t idx = head & AUDIO_RING_MASK;
    memcpy(&rb->buffer[idx * AUDIO_RING_ELEMENT_SIZE], data, AUDIO_RING_ELEMENT_SIZE);
    
    atomic_store_explicit(&rb->head, head + 1, memory_order_relaxed);
    
    return 0;
}

static inline int audio_ring_pop(audio_ring_t *rb, void *out) {
    uint64_t tail = atomic_load_explicit(&rb->tail, memory_order_relaxed);
    uint64_t head = atomic_load_explicit(&rb->head, memory_order_relaxed);
    
    if (head == tail) {
        return -1;
    }
    
    uint32_t idx = tail & AUDIO_RING_MASK;
    memcpy(out, &rb->buffer[idx * AUDIO_RING_ELEMENT_SIZE], AUDIO_RING_ELEMENT_SIZE);
    
    atomic_store_explicit(&rb->tail, tail + 1, memory_order_relaxed);
    
    return 0;
}

static inline void audio_ring_destroy(audio_ring_t *rb) {
    if (rb) {
        free(rb);
    }
}

#endif
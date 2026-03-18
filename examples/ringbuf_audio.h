#ifndef AUDIO_RINGBUF_H
#define AUDIO_RINGBUF_H

#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdatomic.h>

#define AUDIO_RINGBUF_CAPACITY 4096
#define AUDIO_RINGBUF_MASK (AUDIO_RINGBUF_CAPACITY - 1)
#define AUDIO_RINGBUF_ELEMENT_SIZE 4
#define AUDIO_RINGBUF_BUFFER_SIZE (AUDIO_RINGBUF_CAPACITY * AUDIO_RINGBUF_ELEMENT_SIZE)
#define CACHE_LINE_SIZE 64

typedef struct {
    _Atomic(uint64_t) head;
    char pad1[CACHE_LINE_SIZE - sizeof(_Atomic(uint64_t))];
    _Atomic(uint64_t) tail;
    char pad2[CACHE_LINE_SIZE - sizeof(_Atomic(uint64_t))];
    uint8_t buffer[AUDIO_RINGBUF_BUFFER_SIZE];
} audio_ringbuf_t;

static inline audio_ringbuf_t* audio_ringbuf_create(void) {
    audio_ringbuf_t *rb = (audio_ringbuf_t *)malloc(sizeof(audio_ringbuf_t));
    if (!rb) return NULL;
    
    atomic_store_explicit(&rb->head, 0, memory_order_relaxed);
    atomic_store_explicit(&rb->tail, 0, memory_order_relaxed);
    
    return rb;
}

static inline int audio_ringbuf_push(audio_ringbuf_t *rb, const void *data) {
    uint64_t head = atomic_load_explicit(&rb->head, memory_order_relaxed);
    uint64_t tail = atomic_load_explicit(&rb->tail, memory_order_acquire);
    
    if (head - tail >= AUDIO_RINGBUF_CAPACITY) {
        return -1;
    }
    
    uint64_t idx = head & AUDIO_RINGBUF_MASK;
    memcpy(&rb->buffer[idx * AUDIO_RINGBUF_ELEMENT_SIZE], data, AUDIO_RINGBUF_ELEMENT_SIZE);
    
    atomic_store_explicit(&rb->head, head + 1, memory_order_release);
    
    return 0;
}

static inline int audio_ringbuf_pop(audio_ringbuf_t *rb, void *out) {
    uint64_t tail = atomic_load_explicit(&rb->tail, memory_order_relaxed);
    uint64_t head = atomic_load_explicit(&rb->head, memory_order_acquire);
    
    if (tail >= head) {
        return -1;
    }
    
    uint64_t idx = tail & AUDIO_RINGBUF_MASK;
    memcpy(out, &rb->buffer[idx * AUDIO_RINGBUF_ELEMENT_SIZE], AUDIO_RINGBUF_ELEMENT_SIZE);
    
    atomic_store_explicit(&rb->tail, tail + 1, memory_order_release);
    
    return 0;
}

static inline void audio_ringbuf_destroy(audio_ringbuf_t *rb) {
    if (rb) {
        free(rb);
    }
}

#endif
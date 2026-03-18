#ifndef LOG_MSG_QUEUE_H
#define LOG_MSG_QUEUE_H

#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdatomic.h>

#define LOG_MSG_QUEUE_ELEMENT_SIZE 128
#define LOG_MSG_QUEUE_CAPACITY 512
#define LOG_MSG_QUEUE_MASK (LOG_MSG_QUEUE_CAPACITY - 1)
#define LOG_MSG_QUEUE_BUFFER_SIZE (LOG_MSG_QUEUE_CAPACITY * LOG_MSG_QUEUE_ELEMENT_SIZE)
#define CACHE_LINE_SIZE 64

_Static_assert(sizeof(_Atomic(uint64_t)) == 8, "unexpected atomic uint64_t size");
_Static_assert(LOG_MSG_QUEUE_CAPACITY == 512, "capacity must be 512");
_Static_assert(LOG_MSG_QUEUE_MASK == 0x1FF, "mask must be 0x1FF");
_Static_assert((LOG_MSG_QUEUE_CAPACITY & (LOG_MSG_QUEUE_CAPACITY - 1)) == 0, "capacity must be power of two");

typedef struct {
    _Atomic(uint64_t) head;
    uint8_t _pad1[CACHE_LINE_SIZE - sizeof(_Atomic(uint64_t))];
    _Atomic(uint64_t) tail;
    uint8_t _pad2[CACHE_LINE_SIZE - sizeof(_Atomic(uint64_t))];
    uint8_t buffer[LOG_MSG_QUEUE_BUFFER_SIZE];
} log_msg_queue_t;

_Static_assert(sizeof(log_msg_queue_t) == 65664, "unexpected queue size");

static inline log_msg_queue_t *log_msg_queue_create(void) {
    log_msg_queue_t *rb = (log_msg_queue_t *)malloc(sizeof(log_msg_queue_t));
    if (rb) {
        atomic_init(&rb->head, 0);
        atomic_init(&rb->tail, 0);
        memset(rb->buffer, 0, LOG_MSG_QUEUE_BUFFER_SIZE);
    }
    return rb;
}

static inline int log_msg_queue_push(log_msg_queue_t *rb, const void *data) {
    uint64_t head, tail, new_head;
    
    while (1) {
        head = atomic_load_explicit(&rb->head, memory_order_acquire);
        tail = atomic_load_explicit(&rb->tail, memory_order_acquire);
        
        if (head - tail >= LOG_MSG_QUEUE_CAPACITY) {
            return -1;
        }
        
        new_head = head + 1;
        
        if (atomic_compare_exchange_strong_explicit(
                &rb->head, &head, new_head,
                memory_order_release, memory_order_acquire)) {
            uint64_t index = head & LOG_MSG_QUEUE_MASK;
            memcpy(rb->buffer + (index * LOG_MSG_QUEUE_ELEMENT_SIZE),
                   data, LOG_MSG_QUEUE_ELEMENT_SIZE);
            return 0;
        }
    }
}

static inline int log_msg_queue_pop(log_msg_queue_t *rb, void *out) {
    uint64_t tail, head, new_tail;
    
    while (1) {
        tail = atomic_load_explicit(&rb->tail, memory_order_acquire);
        head = atomic_load_explicit(&rb->head, memory_order_acquire);
        
        if (tail >= head) {
            return -1;
        }
        
        new_tail = tail + 1;
        
        if (atomic_compare_exchange_strong_explicit(
                &rb->tail, &tail, new_tail,
                memory_order_release, memory_order_acquire)) {
            uint64_t index = tail & LOG_MSG_QUEUE_MASK;
            memcpy(out, rb->buffer + (index * LOG_MSG_QUEUE_ELEMENT_SIZE),
                   LOG_MSG_QUEUE_ELEMENT_SIZE);
            return 0;
        }
    }
}

static inline void log_msg_queue_destroy(log_msg_queue_t *rb) {
    if (rb) {
        free(rb);
    }
}

#endif
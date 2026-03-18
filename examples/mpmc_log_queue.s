	.file	"tmpbw5xk5jv.c"
	.intel_syntax noprefix
	.text
	.p2align 4
	.globl	_force_create
	.type	_force_create, @function
_force_create:
	endbr64
	push	rbx
	mov	edi, 65664
	call	malloc@PLT
	mov	rbx, rax
	test	rax, rax
	je	.L1
	lea	rdi, 128[rax]
	mov	edx, 65536
	xor	esi, esi
	mov	QWORD PTR [rax], 0
	mov	QWORD PTR 64[rax], 0
	call	memset@PLT
.L1:
	mov	rax, rbx
	pop	rbx
	ret
	.size	_force_create, .-_force_create
	.p2align 4
	.globl	_force_push
	.type	_force_push, @function
_force_push:
	endbr64
	test	rdi, rdi
	je	.L12
	test	rsi, rsi
	je	.L12
.L9:
	mov	rax, QWORD PTR [rdi]
	mov	rdx, rax
	mov	rcx, QWORD PTR 64[rdi]
	sub	rdx, rcx
	cmp	rdx, 511
	ja	.L12
	lea	rdx, 1[rax]
	lock cmpxchg	QWORD PTR [rdi], rdx
	jne	.L9
	sal	rax, 7
	movdqu	xmm0, XMMWORD PTR [rsi]
	and	eax, 65408
	lea	rax, 128[rdi+rax]
	movups	XMMWORD PTR [rax], xmm0
	movdqu	xmm1, XMMWORD PTR 16[rsi]
	movups	XMMWORD PTR 16[rax], xmm1
	movdqu	xmm2, XMMWORD PTR 32[rsi]
	movups	XMMWORD PTR 32[rax], xmm2
	movdqu	xmm3, XMMWORD PTR 48[rsi]
	movups	XMMWORD PTR 48[rax], xmm3
	movdqu	xmm4, XMMWORD PTR 64[rsi]
	movups	XMMWORD PTR 64[rax], xmm4
	movdqu	xmm5, XMMWORD PTR 80[rsi]
	movups	XMMWORD PTR 80[rax], xmm5
	movdqu	xmm6, XMMWORD PTR 96[rsi]
	movups	XMMWORD PTR 96[rax], xmm6
	movdqu	xmm7, XMMWORD PTR 112[rsi]
	movups	XMMWORD PTR 112[rax], xmm7
	xor	eax, eax
	ret
	.p2align 4,,10
	.p2align 3
.L12:
	mov	eax, -1
	ret
	.size	_force_push, .-_force_push
	.p2align 4
	.globl	_force_pop
	.type	_force_pop, @function
_force_pop:
	endbr64
	test	rdi, rdi
	je	.L22
	test	rsi, rsi
	je	.L22
	mov	rdx, QWORD PTR 64[rdi]
	mov	rax, QWORD PTR [rdi]
	cmp	rdx, rax
	je	.L22
	mov	rax, rdx
	add	rdx, 1
	sal	rax, 7
	and	eax, 65408
	lea	rax, 128[rdi+rax]
	movdqu	xmm0, XMMWORD PTR [rax]
	movups	XMMWORD PTR [rsi], xmm0
	movdqu	xmm1, XMMWORD PTR 16[rax]
	movups	XMMWORD PTR 16[rsi], xmm1
	movdqu	xmm2, XMMWORD PTR 32[rax]
	movups	XMMWORD PTR 32[rsi], xmm2
	movdqu	xmm3, XMMWORD PTR 48[rax]
	movups	XMMWORD PTR 48[rsi], xmm3
	movdqu	xmm4, XMMWORD PTR 64[rax]
	movups	XMMWORD PTR 64[rsi], xmm4
	movdqu	xmm5, XMMWORD PTR 80[rax]
	movups	XMMWORD PTR 80[rsi], xmm5
	movdqu	xmm6, XMMWORD PTR 96[rax]
	movups	XMMWORD PTR 96[rsi], xmm6
	movdqu	xmm7, XMMWORD PTR 112[rax]
	xor	eax, eax
	movups	XMMWORD PTR 112[rsi], xmm7
	mov	QWORD PTR 64[rdi], rdx
	ret
	.p2align 4,,10
	.p2align 3
.L22:
	mov	eax, -1
	ret
	.size	_force_pop, .-_force_pop
	.p2align 4
	.globl	_force_destroy
	.type	_force_destroy, @function
_force_destroy:
	endbr64
	test	rdi, rdi
	je	.L23
	jmp	free@PLT
	.p2align 4,,10
	.p2align 3
.L23:
	ret
	.size	_force_destroy, .-_force_destroy
	.ident	"GCC: (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0"
	.section	.note.GNU-stack,"",@progbits
	.section	.note.gnu.property,"a"
	.align 8
	.long	1f - 0f
	.long	4f - 1f
	.long	5
0:
	.string	"GNU"
1:
	.align 8
	.long	0xc0000002
	.long	3f - 2f
2:
	.long	0x3
3:
	.align 8
4:
